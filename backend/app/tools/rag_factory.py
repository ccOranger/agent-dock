"""
RAGFactory：为每个知识库 namespace 构建独立的 RAGTool
"""
import os
import threading

from cachetools import TTLCache
from hello_agents.tools import RAGTool

_rag_cache: TTLCache = TTLCache(maxsize=64, ttl=3600)
# TTLCache 非线程安全：对话主线程与 asyncio.to_thread 工作线程（多库并发检索）会同时访问
_rag_cache_lock = threading.Lock()
KB_ROOT = os.getenv("RAG_KB_ROOT", "./data/knowledge_bases")


def get_rag_tool(namespace: str) -> RAGTool:
    with _rag_cache_lock:
        tool = _rag_cache.get(namespace)
    if tool is not None:
        return tool

    # 构造可能较慢，放在锁外；双重检查避免并发时互相覆盖
    tool = RAGTool(
        knowledge_base_path=KB_ROOT,
        rag_namespace=namespace,
    )
    with _rag_cache_lock:
        existing = _rag_cache.get(namespace)
        if existing is not None:
            return existing
        _rag_cache[namespace] = tool
        return tool


def invalidate_rag_tool(namespace: str) -> None:
    with _rag_cache_lock:
        _rag_cache.pop(namespace, None)
