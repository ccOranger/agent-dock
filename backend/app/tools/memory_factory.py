"""
MemoryFactory：为每个客户构建独立的 MemoryTool
缓存策略：同一 user_id 在 TTL 内复用
"""
from cachetools import TTLCache
from hello_agents.tools import MemoryTool

from app.core.config import settings

_memory_cache: TTLCache = TTLCache(maxsize=2000, ttl=1800)


def get_memory_tool(user_id: str) -> MemoryTool:
    if user_id in _memory_cache:
        return _memory_cache[user_id]

    tool = MemoryTool(
        user_id=user_id,
        memory_types=settings.memory_type_list,
    )
    _memory_cache[user_id] = tool
    return tool


def invalidate_memory_tool(user_id: str) -> None:
    _memory_cache.pop(user_id, None)
