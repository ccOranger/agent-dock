"""用量采集测试：估算口径、与链路开关解耦、写入 SQL 形态。

后台展示已并入「数据分析」页（见 test_admin_analytics.py），这里只锁采集侧行为：
token 估算、关掉链路仍计数、chat_events 回填 + chat_usage_daily 累加。
"""
from datetime import date

import pytest

from app.core.config import settings
from app.core.tracing import TraceCollector
from app.models import repositories as repo_mod
from app.models.repositories import ChatEventRepo
from app.utils import tokens as tk

ZERO_UUID = "00000000-0000-0000-0000-000000000000"


# --------------------------------------------------------------- token 估算
def test_estimate_tokens_empty_is_zero() -> None:
    assert tk.estimate_tokens("") == 0
    assert tk.estimate_tokens(None) == 0


def test_estimate_tokens_char_fallback_is_exact(monkeypatch) -> None:
    """未装 tiktoken（Docker 镜像默认）时的口径：CJK 1 字 1 token，其余 4 字符 1 token。"""
    monkeypatch.setattr(tk, "_encoder_failed", True)
    assert tk.using_tiktoken() is False
    assert tk.estimate_tokens("客服机器人你好") == 7
    assert tk.estimate_tokens("abcdefgh") == 2
    # 中英混排：4 个汉字 + 8 个字符
    assert tk.estimate_tokens("你好世界abcdefgh") == 4 + 2


def test_estimate_tokens_cjk_costs_more_than_ascii() -> None:
    zh = tk.estimate_tokens("客服机器人你好")
    en = tk.estimate_tokens("abcdefgh")
    assert zh >= 1 and en >= 1
    assert zh > en


def test_estimate_round_tokens_sums_both_sides() -> None:
    prompt, completion = "我的订单到哪了", "已为您查询，预计明天送达"
    assert tk.estimate_round_tokens(prompt, completion) == (
        tk.estimate_tokens(prompt) + tk.estimate_tokens(completion)
    )


def test_estimate_tokens_respects_calibration_factor(monkeypatch) -> None:
    text = "客服机器人客服机器人"
    base = tk.estimate_tokens(text)
    monkeypatch.setattr(settings, "token_estimate_factor", 2.0)
    assert tk.estimate_tokens(text) == pytest.approx(base * 2, abs=1)


# ------------------------------------------------------- 采集与链路开关解耦
def test_collector_counts_tokens_even_when_trace_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trace_enabled", False)
    collector = TraceCollector(trace_id="t-usage", scene_code="default")
    assert collector.enabled is False

    collector.record_prompt("用户问：我的订单到哪了")
    collector.record_completion("已为您查询")

    assert collector.prompt_tokens > 0
    assert collector.completion_tokens > 0
    assert collector.total_tokens == collector.prompt_tokens + collector.completion_tokens
    # 链路侧仍是关闭状态：不产 span、不落 request_traces
    assert collector.spans == []
    assert collector.should_persist() is False


# --------------------------------------------------------------- 写入 SQL
class _FakeSession:
    """只记录下发的 SQL，不连库：验证 UPDATE/INSERT 的形态与归属口径。"""

    def __init__(self) -> None:
        self.executed: list = []
        self.committed = False

    async def execute(self, stmt):
        self.executed.append(stmt)

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_record_usage_fills_event_and_upserts_daily(monkeypatch) -> None:
    fake = _FakeSession()
    monkeypatch.setattr(repo_mod, "SessionLocal", lambda: fake)

    await ChatEventRepo.record_usage(
        trace_id="t-usage",
        scene_code="cs_default",
        integration_id=None,  # 匿名/未归属
        tokens=120,
        estimated=True,
        handover=True,
        day=date(2026, 10, 1),
    )

    assert fake.committed
    assert len(fake.executed) == 2
    update_sql = _sql(fake.executed[0])
    assert "UPDATE chat_events" in update_sql
    assert "total_tokens" in update_sql and "tokens_estimated" in update_sql

    upsert_sql = _sql(fake.executed[1])
    assert "INSERT INTO chat_usage_daily" in upsert_sql
    # 同一 (日, 场景, 接入方) 必须累加，而不是覆盖
    assert "ON CONFLICT (day, scene_code, integration_id) DO UPDATE" in upsert_sql
    assert "chat_usage_daily.requests + excluded.requests" in upsert_sql
    # 没有接入方时落零 UUID：PG 唯一约束里 NULL 互不相等，不能用 NULL 当主键列
    assert ZERO_UUID in upsert_sql
    assert "handoffs" in upsert_sql


@pytest.mark.asyncio
async def test_record_usage_skips_event_update_without_trace_id(monkeypatch) -> None:
    fake = _FakeSession()
    monkeypatch.setattr(repo_mod, "SessionLocal", lambda: fake)

    await ChatEventRepo.record_usage(trace_id="", scene_code="default", day=date(2026, 10, 1))

    # 没有被限流/埋点失败兜底时仍需记账，但不必发一条必然 0 行的 UPDATE
    assert len(fake.executed) == 1
    assert "INSERT INTO chat_usage_daily" in _sql(fake.executed[0])
