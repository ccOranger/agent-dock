"""登录失败限流（进程内滑动窗口）。

单实例部署下无需额外依赖；多实例部署时应替换为 Redis 计数。
按 IP 与账号两个维度分别计数：前者防单机撞库，后者防针对单账号的暴力破解。
"""
import threading
import time
from collections import defaultdict, deque

from app.core.exceptions import RateLimited

WINDOW_SECONDS = 300
IP_MAX_FAILURES = 20
ACCOUNT_MAX_FAILURES = 5
# 全量清扫节流：随机用户名/来源会不断新建 key，只在再次访问时裁剪会导致 dict 无界增长
SWEEP_INTERVAL_SECONDS = 60

_lock = threading.Lock()
_failures: dict[str, deque[float]] = defaultdict(deque)
_last_sweep: float = 0.0


def _recent_count(key: str) -> int:
    q = _failures.get(key)
    if not q:
        return 0
    cutoff = time.monotonic() - WINDOW_SECONDS
    while q and q[0] < cutoff:
        q.popleft()
    if not q:
        _failures.pop(key, None)
        return 0
    return len(q)


def check_login_allowed(ip: str, username: str) -> None:
    """在密码校验前调用；窗口内失败次数超阈值直接抛 RateLimited。"""
    with _lock:
        if (
            _recent_count(f"ip:{ip}") >= IP_MAX_FAILURES
            or _recent_count(f"user:{username}") >= ACCOUNT_MAX_FAILURES
        ):
            raise RateLimited("登录失败次数过多，请稍后再试")


def _sweep(now: float) -> None:
    """清掉整个窗口内都没有新失败记录的 key（调用方须持锁）。"""
    cutoff = now - WINDOW_SECONDS
    stale = [k for k, q in _failures.items() if not q or q[-1] < cutoff]
    for k in stale:
        _failures.pop(k, None)


def record_login_failure(ip: str, username: str) -> None:
    global _last_sweep
    now = time.monotonic()
    with _lock:
        if now - _last_sweep >= SWEEP_INTERVAL_SECONDS:
            _sweep(now)
            _last_sweep = now
        _failures[f"ip:{ip}"].append(now)
        _failures[f"user:{username}"].append(now)


def clear_login_failures(username: str) -> None:
    """登录成功只清账号维度；IP 维度按窗口自然衰减，避免"成功一次"即重置撞库额度。"""
    with _lock:
        _failures.pop(f"user:{username}", None)
