"""公开聊天限流：Redis 计数 + 并发流上限。

设计取舍：
- Redis 不可用时 fail-open（放行并每分钟告警一次）：Redis 在启动自检里是软依赖，
  不能因为限流组件故障把聊天整体打死；
- 计数键按 场景 + 主体（user/visitor/ip）分桶，避免一个场景的热点用户挤占其他人；
- 另外两个桶是“整个场景”的分钟/日额度（由场景运行参数给出，0=不限）；
- 全部桶合并到一次 pipeline：本机约 0.2–1ms/ 往返，跨可用区 1–3ms，
  新增额度不能以推迟首字节为代价；
- 并发上限用 INCR/DECR，带 TTL 兜底，进程被强杀时槽位也会自动释放。
"""
import logging
import time

from redis.asyncio import Redis

from app.core.config import settings
from app.core.exceptions import RateLimited
from app.schemas.scene import SceneQuota

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


async def _incr_many(buckets: list[tuple[str, int]]) -> list[int] | None:
    """一次 pipeline 自增全部限流桶，返回与入参等长的计数（None=Redis 不可用）。

    单独抽出来是为了可测试：替掉这个函数就能在不起 Redis 的情况下
    验证“哪个桶超限、回滚了哪些键”。
    """
    client = _redis()
    if client is None:
        return None
    try:
        async with client.pipeline(transaction=False) as pipe:
            for key, ttl in buckets:
                pipe.incr(key)
                pipe.expire(key, ttl)
            results = await pipe.execute()
    except Exception as exc:  # noqa: BLE001 - 限流故障不得影响主流程
        _log_failure(exc)
        return None
    # pipeline 结果按 incr/expire 成对返回，取偶数位即 INCR 的计数
    return [int(results[i * 2]) for i in range(len(buckets))]


async def _decr_many(keys: list[str]) -> None:
    """超限时回滚本次已自增的计数：否则用户被扣了次数却只收到 429。

    必须回滚本次 INCR 过的**全部**键（含主体日桶、场景日桶与并发槽）：
    只回滚到失败桶为止会让后面的桶永久多计一个——尤其并发槽，
    它带 300s TTL 且每次重试都会刷新，用户会被“当前对话尚未结束”锁死。
    回滚到 0 及以下直接删键，避免跨分钟/跨天留下没有 TTL 的负计数。
    """
    client = _redis()
    if client is None or not keys:
        return
    try:
        async with client.pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.decr(key)
            values = await pipe.execute()
        drained = [key for key, value in zip(keys, values, strict=True) if int(value) <= 0]
        if drained:
            await client.delete(*drained)
    except Exception as exc:  # noqa: BLE001 - 回滚失败只影响计数精度
        _log_failure(exc)


async def acquire_chat_slot(
    scene_code: str,
    actor: str,
    quota: SceneQuota | None = None,
) -> ChatSlot:
    """进入聊天前的五道闸：主体分钟/日、场景分钟/日、并发流。

    - 主体额度来自全局环境变量（按 user/visitor/ip 分桶）；
    - 场景额度来自场景运行参数（整场景共享，0 = 不限）；
    - 五道闸合并成一次 Redis 往返，任一超限就回滚本次自增并抛 RateLimited(4029/429)；
    - Redis 故障时全部放行。
    """
    if not settings.redis_url:
        return ChatSlot(None)

    minute_bucket = int(time.time() // 60)
    day = time.strftime("%Y%m%d")

    # (key, ttl, limit, 超限文案)；limit<=0 表示不限，不建桶也就没有额外往返
    checks: list[tuple[str, int, int, str]] = []
    if settings.public_chat_rate_limit_per_minute > 0:
        limit = settings.public_chat_rate_limit_per_minute
        checks.append(
            (
                f"rl:chat:m:{scene_code}:{actor}:{minute_bucket}",
                120,
                limit,
                f"请求过于频繁，请稍后再试（每分钟上限 {limit} 次）",
            )
        )
    if settings.public_chat_daily_quota > 0:
        limit = settings.public_chat_daily_quota
        checks.append(
            (
                f"rl:chat:d:{scene_code}:{actor}:{day}",
                172800,
                limit,
                f"今日对话次数已达上限（{limit} 次）",
            )
        )
    if quota is not None and quota.per_minute > 0:
        limit = quota.per_minute
        checks.append(
            (
                f"rl:chat:sm:{scene_code}:{minute_bucket}",
                120,
                limit,
                f"本场景请求过于频繁，请稍后再试（每分钟上限 {limit} 次）",
            )
        )
    if quota is not None and quota.daily > 0:
        limit = quota.daily
        checks.append(
            (
                f"rl:chat:sd:{scene_code}:{day}",
                172800,
                limit,
                f"本场景今日对话额度已用完（上限 {limit} 次），请联系管理员",
            )
        )

    concurrent_key: str | None = None
    if settings.public_chat_max_concurrent > 0:
        concurrent_key = f"rl:chat:c:{scene_code}:{actor}"
        checks.append(
            (
                concurrent_key,
                300,
                settings.public_chat_max_concurrent,
                "当前对话尚未结束，请等待上一条回复完成",
            )
        )

    if not checks:
        return ChatSlot(None)

    counts = await _incr_many([(key, ttl) for key, ttl, _, _ in checks])
    if counts is None:
        # Redis 不可用（含未配置）：fail-open，与旧版本一致
        return ChatSlot(None)

    for (_, _, limit, message), count in zip(checks, counts, strict=True):
        if count > limit:
            # 全部桶都已 INCR，必须全部回滚（含未参与判定的后续桶与并发槽）
            await _decr_many([item[0] for item in checks])
            raise RateLimited(message)

    # 并发槽位检查通过才占位；其余桶是纯计数，无需释放
    if concurrent_key is not None:
        return ChatSlot(concurrent_key)
    return ChatSlot(None)


async def close_redis() -> None:
    """进程退出时释放连接；测试也可调用以隔离环境。"""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None
