"""公开聊天限流：Redis 计数 + 并发流上限。

设计取舍：
- Redis 不可用时 fail-open（放行并每分钟告警一次）：Redis 在启动自检里是软依赖，
  不能因为限流组件故障把聊天整体打死；
- 计数键按 场景 + 主体（user/visitor/ip）分桶，避免一个场景的热点用户挤占其他人；
- 并发上限用 INCR/DECR，带 TTL 兜底，进程被强杀时槽位也会自动释放。
"""
import logging
import time

from redis.asyncio import Redis

from app.core.config import settings
from app.core.exceptions import RateLimited

logger = logging.getLogger(__name__)

_client: Redis | None = None
_last_failure_log = 0.0
_FAILURE_LOG_INTERVAL = 60.0


def _redis() -> Redis | None:
    global _client
    if not settings.redis_url:
        return None
    if _client is None:
        _client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    return _client


def _log_failure(exc: Exception) -> None:
    global _last_failure_log
    now = time.monotonic()
    if now - _last_failure_log >= _FAILURE_LOG_INTERVAL:
        _last_failure_log = now
        logger.warning("限流 Redis 不可用，已放行本轮请求: %s", exc)


async def _incr(key: str, ttl: int) -> int | None:
    client = _redis()
    if client is None:
        return None
    try:
        async with client.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl)
            count, _ = await pipe.execute()
        return int(count)
    except Exception as exc:  # noqa: BLE001 - 限流故障不得影响主流程
        _log_failure(exc)
        return None


async def _decr(key: str) -> None:
    client = _redis()
    if client is None:
        return
    try:
        value = await client.decr(key)
        if value <= 0:
            await client.delete(key)
    except Exception as exc:  # noqa: BLE001
        _log_failure(exc)


class ChatSlot:
    """一轮流式对话占用的并发槽位；结束（含异常/断连）时必须 release。"""

    def __init__(self, key: str | None):
        self._key = key
        self._released = False

    async def release(self) -> None:
        if self._released or self._key is None:
            return
        self._released = True
        await _decr(self._key)


async def acquire_chat_slot(scene_code: str, actor: str) -> ChatSlot:
    """进入聊天前的三道闸：分钟频次、日配额、并发流。

    超限统一抛 RateLimited(4029/429)；Redis 故障时全部放行。
    """
    if not settings.redis_url:
        return ChatSlot(None)

    minute_limit = settings.public_chat_rate_limit_per_minute
    daily_limit = settings.public_chat_daily_quota
    concurrent_limit = settings.public_chat_max_concurrent

    if minute_limit > 0:
        bucket = int(time.time() // 60)
        count = await _incr(f"rl:chat:m:{scene_code}:{actor}:{bucket}", 120)
        if count is not None and count > minute_limit:
            raise RateLimited(f"请求过于频繁，请稍后再试（每分钟上限 {minute_limit} 次）")

    if daily_limit > 0:
        day = time.strftime("%Y%m%d")
        count = await _incr(f"rl:chat:d:{scene_code}:{actor}:{day}", 172800)
        if count is not None and count > daily_limit:
            raise RateLimited(f"今日对话次数已达上限（{daily_limit} 次）")

    if concurrent_limit > 0:
        key = f"rl:chat:c:{scene_code}:{actor}"
        count = await _incr(key, 300)
        if count is not None and count > concurrent_limit:
            await _decr(key)
            raise RateLimited("当前对话尚未结束，请等待上一条回复完成")
        if count is not None:
            return ChatSlot(key)

    return ChatSlot(None)


async def close_redis() -> None:
    """进程退出时释放连接；测试也可调用以隔离环境。"""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None
