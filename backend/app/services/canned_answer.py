"""固定话术的知识库回答：检索 → 裁剪 → 按固定框架拼接（不经过模型改写）。

为什么不做 LLM 改写：
- 固定话术的卖点是"措辞与事实完全可控、延迟低"。交给模型改写等于把可控性交回去，
  还多一次 LLM 调用（秒级）；这里只做检索结果的机械拼接。
- 知识库覆盖不到时立即回退到话术正文，两者都空就让本轮继续走正常路由，
  任何失败都不向客户抛错。

拼接格式（固定）：

    引导语（reply_text，可选）

    1. 片段一
    2. 片段二
    3. 片段三
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from app.agents.canned_scripts import CannedScript, render_script_text
from app.models.repositories import KnowledgeBaseRepo
from app.schemas.scene import SceneRuntime
from app.services.rag_service import RagService

logger = logging.getLogger(__name__)

KB_TOP_K = 3
MAX_CHUNK_CHARS = 180
MAX_TOTAL_CHARS = 800
MAX_CONCURRENCY = 4
# 固定话术对延迟敏感：即使场景配了 15 秒，命中话术时也只等 3 秒
KB_TIMEOUT_CAP = 3
# 启用中知识库清单的进程内缓存；kb 话术命中在首字节路径上，不该每轮都全表查一次
BASE_CACHE_TTL = 10.0
_base_cache: tuple[float, tuple[str, ...]] | None = None

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class CannedKbAnswer:
    """知识库话术的检索结果；text 为空表示调用方应回退。"""

    text: str
    namespaces: tuple[str, ...] = ()
    chunks: int = 0
    # ok=拼装成功；empty=零命中；error=全部检索失败；no_namespace=没有可用知识库
    status: str = "ok"
    notes: tuple[str, ...] = field(default_factory=tuple)


def _clean(content: str) -> str:
    """折叠空白并截断：知识库分块常带换行与缩进，直接拼接会出现大片留白。"""
    text = _WHITESPACE.sub(" ", (content or "")).strip()
    if len(text) > MAX_CHUNK_CHARS:
        return text[:MAX_CHUNK_CHARS].rstrip() + "…"
    return text


def _merge(hits: list[dict]) -> list[str]:
    """按分数降序去重并用固定序号拼装正文；总长超过上限就截断条数。"""
    ordered = sorted(hits, key=lambda h: float(h.get("score") or 0), reverse=True)
    seen: set[str] = set()
    chunks: list[str] = []
    for hit in ordered:
        text = _clean(str(hit.get("content") or ""))
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        chunks.append(text)
        if len(chunks) >= KB_TOP_K:
            break

    lines: list[str] = []
    total = 0
    for idx, chunk in enumerate(chunks, 1):
        line = f"{idx}. {chunk}"
        if total + len(line) > MAX_TOTAL_CHARS:
            break
        lines.append(line)
        total += len(line)
    return lines


def _reset_base_cache() -> None:
    """清空启用库清单缓存（测试与知识库增删改后可主动调用）。"""
    global _base_cache
    _base_cache = None


async def _active_namespaces(candidates: tuple[str, ...]) -> tuple[str, ...]:
    """过滤掉不存在或已停用的知识库：配错库不该让整轮对话静默变成"不知道"。

    启用库清单按 `BASE_CACHE_TTL` 进程内缓存：这一步在首字节路径上，
    每次命中话术都打一次 knowledge_bases 全表不值当；停用库最多晚 10s 生效。
    """
    global _base_cache
    if not candidates:
        return ()
    now = time.monotonic()
    cached = _base_cache
    if cached is None or now - cached[0] > BASE_CACHE_TTL:
        rows = await KnowledgeBaseRepo.list_all()
        cached = (now, tuple(row.namespace for row in rows if row.is_active))
        _base_cache = cached
    active = set(cached[1])
    return tuple(ns for ns in candidates if ns in active)


async def answer_from_kb(
    scene,
    script: CannedScript,
    runtime: SceneRuntime | None,
    query: str,
) -> CannedKbAnswer:
    """检索话术绑定的知识库并拼装回答；失败一律返回空文本由调用方降级。"""
    active = await _active_namespaces(tuple(script.kb_namespaces))
    if not active:
        return CannedKbAnswer(text="", status="no_namespace")

    timeout = min(runtime.rag_timeout if runtime else KB_TIMEOUT_CAP, KB_TIMEOUT_CAP)
    min_score = runtime.rag_min_score if runtime else 0.3
    rag = RagService()
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    failures: list[str] = []

    async def _search(namespace: str) -> list[dict]:
        async with semaphore:
            try:
                return await rag.search(
                    namespace,
                    query,
                    top_k=KB_TOP_K,
                    min_score=min_score,
                    advanced=False,
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001 - 单库失败不影响其它库
                failures.append(f"{namespace}: {exc}")
                return []

    results = await asyncio.gather(*(_search(ns) for ns in active))
    hits = [hit for group in results for hit in group]
    lines = _merge(hits)
    if not lines:
        status = "error" if len(failures) == len(active) else "empty"
        if failures:
            logger.warning(f"固定话术知识库检索失败（已回退）: {failures}")
        return CannedKbAnswer(
            text="",
            namespaces=active,
            chunks=0,
            status=status,
            notes=tuple(failures),
        )

    intro = render_script_text(script.text, scene).strip()
    body = "\n".join(lines)
    answer = f"{intro}\n\n{body}" if intro else body
    return CannedKbAnswer(
        text=answer,
        namespaces=active,
        chunks=len(lines),
        status="ok",
        notes=tuple(failures),
    )
