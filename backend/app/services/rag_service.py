"""RagService：包装 RAGTool 检索"""
import asyncio

from app.core.config import settings
from app.tools.rag_factory import get_rag_tool


class RagService:
    async def search(self, namespace: str, query: str, top_k: int | None = None):
        ns = namespace or settings.rag_default_namespace
        tool = get_rag_tool(ns)
        return await asyncio.to_thread(
            tool.run,
            {
                "action": "search",
                "query": query,
                "limit": top_k or settings.rag_top_k,
                "min_score": settings.rag_min_score,
                "namespace": ns,
            },
        )

    async def list_documents(self, namespace: str):
        tool = get_rag_tool(namespace)
        return await asyncio.to_thread(
            tool.run, {"action": "stats", "namespace": namespace}
        )

    async def reindex(self, namespace: str):
        tool = get_rag_tool(namespace)
        return await asyncio.to_thread(
            tool.run, {"action": "reindex", "namespace": namespace}
        )
