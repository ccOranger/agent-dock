"""
MemoryFactory：为每个客户构建独立的 MemoryTool
缓存策略：同一 user_id 在 TTL 内复用
"""
import threading

from cachetools import TTLCache
from hello_agents.memory.base import MemoryConfig
from hello_agents.tools import MemoryTool

from app.core.config import settings

_memory_cache: TTLCache = TTLCache(maxsize=2000, ttl=1800)
# TTLCache 非线程安全：检索/归档在 asyncio.to_thread 工作线程中执行，会与主线程并发访问
_memory_cache_lock = threading.Lock()


def get_memory_tool(user_id: str) -> MemoryTool:
    with _memory_cache_lock:
        tool = _memory_cache.get(user_id)
    if tool is not None:
        return tool

    # 构造可能较慢，放在锁外；双重检查避免并发时互相覆盖
    # 按 spec 8.1 把 working 层 TTL 接为 30 分钟（库默认 120，settings.working_memory_ttl 单位秒）
    memory_config = MemoryConfig(working_memory_ttl_minutes=max(settings.working_memory_ttl // 60, 1))
    tool = MemoryTool(
        user_id=user_id,
        memory_config=memory_config,
        memory_types=settings.memory_type_list,
    )
    with _memory_cache_lock:
        existing = _memory_cache.get(user_id)
        if existing is not None:
            return existing
        _memory_cache[user_id] = tool
        return tool


def invalidate_memory_tool(user_id: str) -> None:
    with _memory_cache_lock:
        _memory_cache.pop(user_id, None)
