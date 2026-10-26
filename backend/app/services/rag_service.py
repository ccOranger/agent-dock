"""RagService：包装 RAGTool 检索 + Qdrant 分块浏览"""
import asyncio
import os

from app.core.config import settings
from app.services import qdrant_helper
from app.tools.rag_factory import get_rag_tool
from app.utils.async_io import run_in_pool


class RagService:
    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int | None = None,
        min_score: float | None = None,
        advanced: bool = True,
        timeout: int | None = None,
    ) -> list[dict]:
        """检索知识库，返回结构化结果列表。

        直接调 RAGTool 内部的 pipeline（而非 tool.run("search")），
        因为后者返回的是给人看的格式化字符串，不适合前端渲染。

        timeout 由调用方决定：场景运行参数可覆盖全局 RAG_TIMEOUT（如固定话术只给 3 秒）。
        """
        ns = namespace or settings.rag_default_namespace
        tool = get_rag_tool(ns)
        limit = top_k or settings.rag_top_k
        threshold = settings.rag_min_score if min_score is None else min_score

        def _run() -> list[dict]:
            pipeline = tool._get_pipeline(ns)
            if advanced:
                results = pipeline["search_advanced"](
                    query=query,
                    top_k=limit,
                    enable_mqe=True,
                    enable_hyde=True,
                    score_threshold=threshold or None,
                )
            else:
                results = pipeline["search"](
                    query=query,
                    top_k=limit,
                    score_threshold=threshold or None,
                )
            out: list[dict] = []
            for r in results or []:
                meta = r.get("metadata", {}) or {}
                source = meta.get("source_path") or ""
                out.append(
                    {
                        "content": meta.get("content", ""),
                        "score": r.get("score", 0.0),
                        "source": source,
                        "source_name": os.path.basename(source) if source else None,
                        "heading_path": meta.get("heading_path"),
                    }
                )
            return out

        # 硬超时：Qdrant/embedding 挂起时抛 TimeoutError，由上层（对话链路）降级为空
        return await asyncio.wait_for(
            run_in_pool(_run), timeout=timeout or settings.rag_timeout
        )

    async def list_documents(self, namespace: str):
        tool = get_rag_tool(namespace)
        return await run_in_pool(tool.run, {"action": "stats", "namespace": namespace})

    async def reindex(self, namespace: str):
        tool = get_rag_tool(namespace)
        return await run_in_pool(tool.run, {"action": "reindex", "namespace": namespace})

    async def list_chunks(
        self,
        namespace: str,
        source_path: str | None = None,
        limit: int = 50,
        offset=None,
    ) -> dict:
        """浏览已索引分块（直连 Qdrant）。source_path 传入则按文档过滤。"""
        ns = namespace or settings.rag_default_namespace
        chunks, next_offset = await run_in_pool(
            qdrant_helper.scroll_chunks, ns, source_path, limit, offset
        )
        return {
            "chunks": chunks,
            "next_offset": (str(next_offset) if next_offset is not None else None),
        }
