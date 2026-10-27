"""场景运行参数（scenes.runtime_config）：规范化、校验与解析。

为什么单独一层：
- 存储只放"显式覆盖项"（null / 缺失 = 继承全局），解析时再合并 Settings，
  这样全局默认改了，没有覆盖的场景自动跟随；
- 解析必须是纯函数、不查库：一轮对话里会被 Orchestrator / ChatService / ContextEngineer
  各取一次，多一次查库就多一次热路径开销；
- 校验白名单化：未知键直接 400 而不是静默丢弃，避免"配了但没生效"的排查灾难。

配置结构（本期）：

    {
      "rag": {"inject": true, "top_k": 5, "min_score": 0.3, "timeout": 15,
              "namespaces": ["cs_order"]},
      "history_limit": 50,
      "quota": {"per_minute": null, "daily": 5000}
    }

路由模式 / 路由模型 / 阈值 / 意图上限不在这里：它们继续用 scenes 的既有列，
避免同一配置两处维护。
"""
import logging

from app.core.config import settings
from app.core.exceptions import ParamError
from app.models.repositories import KnowledgeBaseRepo
from app.schemas.scene import DEFAULT_HISTORY_LIMIT, SceneQuota, SceneRuntime

logger = logging.getLogger(__name__)

TOP_KEYS = ("rag", "history_limit", "quota")
RAG_KEYS = ("inject", "top_k", "min_score", "timeout", "namespaces")
QUOTA_KEYS = ("per_minute", "daily")

MAX_NAMESPACES = 10
_RANGES = {
    "rag.top_k": (1, 20),
    "rag.timeout": (1, 60),
    "history_limit": (1, DEFAULT_HISTORY_LIMIT),
    "quota.per_minute": (0, 600),
    "quota.daily": (0, 100000),
}


def _as_int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParamError(f"{field} 必须是整数")
    low, high = _RANGES[field]
    if value < low or value > high:
        raise ParamError(f"{field} 取值范围 {low}-{high}")
    return value


def _as_float(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParamError(f"{field} 必须是数字")
    number = float(value)
    if number < 0 or number > 1:
        raise ParamError(f"{field} 取值范围 0-1")
    return number


def _as_bool(value, field: str) -> bool:
    if not isinstance(value, bool):
        raise ParamError(f"{field} 必须是布尔值")
    return value


def _check_keys(payload: dict, allowed: tuple[str, ...], prefix: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ParamError(f"{prefix} 包含未知键: {unknown}")


def normalize_runtime_config(payload: dict | None) -> dict:
    """校验并归一化入参；null / 缺失 = 继承全局，返回的对象只保留显式覆盖项。

    整对象替换语义：PATCH 传 ``{}`` 或 ``null`` 等于恢复"全部继承"。
    """
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ParamError("runtime_config 必须是对象")
    _check_keys(payload, TOP_KEYS, "runtime_config")

    out: dict = {}

    rag_in = payload.get("rag")
    if rag_in is not None:
        if not isinstance(rag_in, dict):
            raise ParamError("runtime_config.rag 必须是对象")
        _check_keys(rag_in, RAG_KEYS, "runtime_config.rag")
        rag: dict = {}
        if rag_in.get("inject") is not None:
            rag["inject"] = _as_bool(rag_in["inject"], "rag.inject")
        if rag_in.get("top_k") is not None:
            rag["top_k"] = _as_int(rag_in["top_k"], "rag.top_k")
        if rag_in.get("min_score") is not None:
            rag["min_score"] = _as_float(rag_in["min_score"], "rag.min_score")
        if rag_in.get("timeout") is not None:
            rag["timeout"] = _as_int(rag_in["timeout"], "rag.timeout")
        if rag_in.get("namespaces") is not None:
            raw = rag_in["namespaces"]
            if not isinstance(raw, list):
                raise ParamError("rag.namespaces 必须是数组")
            cleaned: list[str] = []
            for item in raw:
                if not isinstance(item, str) or not item.strip():
                    raise ParamError("rag.namespaces 只能包含非空字符串")
                cleaned.append(item.strip())
            cleaned = list(dict.fromkeys(cleaned))
            if len(cleaned) > MAX_NAMESPACES:
                raise ParamError(f"rag.namespaces 最多 {MAX_NAMESPACES} 个")
            rag["namespaces"] = cleaned
        if rag:
            out["rag"] = rag

    if payload.get("history_limit") is not None:
        out["history_limit"] = _as_int(payload["history_limit"], "history_limit")

    quota_in = payload.get("quota")
    if quota_in is not None:
        if not isinstance(quota_in, dict):
            raise ParamError("runtime_config.quota 必须是对象")
        _check_keys(quota_in, QUOTA_KEYS, "runtime_config.quota")
        quota: dict = {}
        for key in QUOTA_KEYS:
            if quota_in.get(key) is not None:
                quota[key] = _as_int(quota_in[key], f"quota.{key}")
        if quota:
            out["quota"] = quota

    return out


async def validate_runtime_namespaces(config: dict) -> None:
    """rag.namespaces 必须是存在且启用中的知识库（缺失/停用都拒绝，避免静默零命中）。"""
    namespaces = ((config or {}).get("rag") or {}).get("namespaces") or []
    if not namespaces:
        return
    rows = await KnowledgeBaseRepo.list_all()
    active = {row.namespace for row in rows if row.is_active}
    missing = [ns for ns in namespaces if ns not in active]
    if missing:
        raise ParamError(f"以下知识库不存在或已停用: {missing}")


def resolve_runtime(config: dict | None) -> SceneRuntime:
    """合并全局默认，产出运行快照与每个键的来源（scene / global）。"""
    cfg = config or {}
    rag = cfg.get("rag") or {}
    quota_cfg = cfg.get("quota") or {}
    sources: dict[str, str] = {}

    def pick(container: dict, key: str, global_value, label: str, default_source: str = "global"):
        if key in container:
            sources[label] = "scene"
            return container[key]
        sources[label] = default_source
        return global_value

    rag_inject = rag.get("inject", True)
    sources["rag.inject"] = "scene" if "inject" in rag else "global"
    rag_top_k = pick(rag, "top_k", settings.rag_top_k, "rag.top_k")
    rag_min_score = pick(rag, "min_score", settings.rag_min_score, "rag.min_score")
    rag_timeout = pick(rag, "timeout", settings.rag_timeout, "rag.timeout")
    rag_namespaces = tuple(pick(rag, "namespaces", [], "rag.namespaces") or ())
    history_limit = pick(cfg, "history_limit", DEFAULT_HISTORY_LIMIT, "history_limit")

    # 场景总额度默认不限（0）：全局 Settings 是“单主体”限流，两者对象不同，
    # 把 30 次/分 当成“整个场景”的默认额度会让多用户的场景莫名被限。
    per_minute = pick(quota_cfg, "per_minute", 0, "quota.per_minute", "default")
    daily = pick(quota_cfg, "daily", 0, "quota.daily", "default")

    return SceneRuntime(
        rag_inject=bool(rag_inject),
        rag_top_k=int(rag_top_k),
        rag_min_score=float(rag_min_score),
        rag_timeout=int(rag_timeout),
        rag_namespaces=rag_namespaces,
        history_limit=int(history_limit),
        quota=SceneQuota(per_minute=int(per_minute), daily=int(daily), sources=dict(sources)),
        sources=sources,
    )


def effective_runtime(scene) -> SceneRuntime:
    """取一轮对话生效的运行参数；兼容手工构造（无 runtime 字段）的旧测试快照。"""
    runtime = getattr(scene, "runtime", None) if scene is not None else None
    if isinstance(runtime, SceneRuntime):
        return runtime
    config = getattr(scene, "runtime_config", None) if scene is not None else None
    return resolve_runtime(config or {})


def runtime_effective_dict(runtime: SceneRuntime) -> dict:
    """后台展示用：生效值（含继承来的全局默认）。"""
    return {
        "rag": {
            "inject": runtime.rag_inject,
            "top_k": runtime.rag_top_k,
            "min_score": runtime.rag_min_score,
            "timeout": runtime.rag_timeout,
            "namespaces": list(runtime.rag_namespaces),
        },
        "history_limit": runtime.history_limit,
        "quota": {"per_minute": runtime.quota.per_minute, "daily": runtime.quota.daily},
    }
