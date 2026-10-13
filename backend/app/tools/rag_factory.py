"""
RAGFactory：为每个知识库 namespace 构建独立的 RAGTool
"""
import os

from cachetools import TTLCache
from hello_agents.tools import RAGTool

_rag_cache: TTLCache = TTLCache(maxsize=64, ttl=3600)
KB_ROOT = os.getenv("RAG_KB_ROOT", "./data/knowledge_bases")


def get_rag_tool(namespace: str) -> RAGTool:
    if namespace in _rag_cache:
        return _rag_cache[namespace]

    tool = RAGTool(
        knowledge_base_path=KB_ROOT,
        rag_namespace=namespace,
    )
    _rag_cache[namespace] = tool
    return tool


def invalidate_rag_tool(namespace: str) -> None:
    _rag_cache.pop(namespace, None)
