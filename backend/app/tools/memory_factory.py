"""MemoryFactory：为每个客户构建独立的记忆工具。

缓存策略：同一 user_id 在 TTL 内复用（构造会连 Qdrant，代价不低）。
P1 起本模块不再直接依赖 hello_agents，实现由 ``core.ports.adapters`` 提供。
"""
import threading

from cachetools import TTLCache

from app.core.config import settings
from app.core.ports.adapters.hello_agents import create_memory_config, create_memory_tool
from app.core.ports.protocols import MemoryPort

_memory_cache: TTLCache = TTLCache(maxsize=2000, ttl=1800)
# TTLCache 非线程安全：检索/归档在 asyncio.to_thread 工作线程中执行，会与主线程并发访问
_memory_cache_lock = threading.Lock()


def get_memory_tool(user_id: str) -> MemoryPort:
    with _memory_cache_lock:
        tool = _memory_cache.get(user_id)
    if tool is not None:
        return tool

    # 构造可能较慢，放在锁外；双重检查避免并发时互相覆盖
    # 按 spec 8.1 把 working 层 TTL 接为 30 分钟（库默认 120，settings.working_memory_ttl 单位秒）
    memory_config = create_memory_config(working_memory_ttl_minutes=max(settings.working_memory_ttl // 60, 1))
    tool = create_memory_tool(
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
