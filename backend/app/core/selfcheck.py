"""启动自检：数据库 / Redis / Qdrant / 模型 连通性。

目标（M2-6）：启动日志能一眼看出依赖是否就绪；失败给可操作提示，
而不是让用户在后续某个请求里才看到 “connection refused”。

- 数据库：硬依赖，失败/超时直接中断启动（后续所有功能都不可用）
- Qdrant：硬依赖（hello-agents 记忆/RAG 初始化会立即连接 Qdrant，无法降级）
- Redis / 模型：软依赖，失败只告警（缓存/工作记忆、生成会降级，但服务可起）
"""
import asyncio
import logging
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0
# DB 探测单独放宽：冷启动/云库首连可能慢于 HTTP 探活，但不能无限等（asyncpg 默认 60s）
_DB_TIMEOUT = _TIMEOUT * 2


def _host_of(url: str) -> str:
    """只打印 host:port，避免把 DSN 里的口令写进日志。"""
    if not url:
        return "-"
    try:
        parts = urlsplit(url.replace("+asyncpg", ""))
        return parts.hostname + (f":{parts.port}" if parts.port else "") if parts.hostname else "-"
    except ValueError:
        return "-"


def _redis_target(url: str) -> str:
    return _host_of(url) if url else "-"


async def _check_database() -> str:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return f"OK（{_host_of(settings.database_url)}）"
    except Exception as exc:  # noqa: BLE001 - 自检要把底层异常转成可读提示
        raise RuntimeError(
            f"数据库连接失败（{_host_of(settings.database_url)}）：{exc.__class__.__name__}: {exc}\n"
            "  排查：DATABASE_URL 是否正确、数据库是否已启动、容器内是否指向服务名（compose 下为 postgres:5432）"
        ) from exc


async def _check_redis() -> str:
    if not settings.redis_url:
        return "未配置（跳过）"
    client = None
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(settings.redis_url, socket_connect_timeout=_TIMEOUT)
        await client.ping()
        return f"OK（{_redis_target(settings.redis_url)}）"
    except Exception as exc:  # noqa: BLE001
        return (
            f"不可用（{_redis_target(settings.redis_url)}）：{exc.__class__.__name__}\n"
            "  影响：依赖 Redis 的缓存/工作记忆降级；单实例部署可继续运行"
        )
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 - 关闭失败不影响自检结论
                pass


async def _check_qdrant() -> str:
    if not settings.qdrant_url:
        return "未配置（跳过；记忆/RAG 需设置 QDRANT_URL）"
    base = settings.qdrant_url.rstrip("/")
    headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{base}/collections", headers=headers)
            resp.raise_for_status()
            collections = resp.json().get("result", {}).get("collections", [])
        return f"OK（{_host_of(base)}，{len(collections)} 个集合）"
    except Exception as exc:  # noqa: BLE001
        return (
            f"不可用（{_host_of(base)}）：{exc.__class__.__name__}\n"
            "  影响：记忆与知识库检索不可用；请确认 QDRANT_URL 可达（compose 下为 http://qdrant:6333）"
        )


async def check_model() -> str:
    """对 OpenAI 兼容接口做一次轻量连通性检查（GET /models）。"""
    if not settings.llm_api_key or settings.llm_api_key.strip() in {"sk-xxx", "change_me"}:
        return "未配置（LLM_API_KEY 为空或仍是占位值）"
    base = settings.llm_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{base}/models", headers={"Authorization": f"Bearer {settings.llm_api_key}"}
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
        names = [m.get("id", "") for m in data][:3]
        suffix = f"，如 {', '.join(n for n in names if n)}" if names else ""
        return f"OK（{_host_of(base)}{suffix}）"
    except Exception as exc:  # noqa: BLE001
        return (
            f"不可用（{_host_of(base)}）：{exc.__class__.__name__}\n"
            "  影响：对话生成不可用；检查 LLM_API_KEY / LLM_BASE_URL，或先用 /docs 验证网络"
        )


async def run_startup_selfcheck() -> None:
    """启动时执行；数据库/Qdrant 不可用会中断启动，其余只记录告警。"""
    try:
        db = await asyncio.wait_for(_check_database(), timeout=_DB_TIMEOUT)
    except TimeoutError as exc:
        raise RuntimeError(
            f"启动中止：数据库探测超时（>{_DB_TIMEOUT:.0f}s，{_host_of(settings.database_url)}）\n"
            "  排查：数据库是否已启动、网络/防火墙是否放行（compose 下为 postgres:5432）"
        ) from exc
    logger.info("[自检] 数据库: %s", db)

    for name, check in (
        ("Redis", _check_redis),
        ("Qdrant", _check_qdrant),
        ("模型", check_model),
    ):
        result = await check()
        if result.startswith("OK") or result.startswith("未配置"):
            logger.info("[自检] %s: %s", name, result)
        else:
            logger.warning("[自检] %s: %s", name, result)
            # 记忆/RAG 初始化硬依赖 Qdrant：与其让 hello-agents 抛一大段堆栈，
            # 不如在这里给出单行可操作错误并中止启动
            if name == "Qdrant":
                raise RuntimeError(f"启动中止：Qdrant {result}")
