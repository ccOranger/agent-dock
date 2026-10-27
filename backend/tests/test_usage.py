"""用量计量与报表测试：估算口径、与链路开关解耦、写入 SQL 形态、接口参数与 RBAC、CSV 编码"""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import admin_usage as au
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


# --------------------------------------------------------------- 接口
@pytest.fixture
def client(make_test_app):
    app = make_test_app([au.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


SAMPLE_ROWS = [
    {
        "day": "2026-10-01",
        "scene_code": "cs_default",
        "integration_id": ZERO_UUID,
        "integration_name": None,
        "requests": 3,
        "tokens": 900,
        "estimated_requests": 3,
        "handoffs": 1,
        "errors": 0,
    }
]


def _patch_summary(monkeypatch, captured: dict) -> None:
    async def fake_summary(**kw):
        captured.update(kw)
        return {
            "rows": [{"day": "2026-10-01", **{k: 1 for k in ("requests", "tokens")}}],
            "totals": {"requests": 1, "tokens": 120, "estimated_requests": 1},
        }

    monkeypatch.setattr(au.UsageRepo, "summary", fake_summary)


def test_summary_passes_range_and_dimension(client, monkeypatch) -> None:
    captured: dict = {}
    _patch_summary(monkeypatch, captured)

    resp = client.get(
        "/admin/usage/summary",
        params={"from": "2026-10-01", "to": "2026-10-02", "group_by": "scene", "scene_code": "cs_default"},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["from"] == "2026-10-01" and data["to"] == "2026-10-02"
    assert data["group_by"] == "scene" and data["scene_code"] == "cs_default"
    # 当前口径是估算值，接口必须显式告诉调用方，避免被当成供应商账单
    assert data["tokens_estimated"] is True
    assert captured["date_from"] == date(2026, 10, 1)
    assert captured["date_to"] == date(2026, 10, 2)
    assert captured["group_by"] == "scene"


def test_summary_defaults_to_last_30_days(client, monkeypatch) -> None:
    captured: dict = {}
    _patch_summary(monkeypatch, captured)

    resp = client.get("/admin/usage/summary")
    assert resp.status_code == 200
    today = datetime.now().astimezone().date()
    assert captured["date_to"] == today
    assert captured["date_from"] == today - timedelta(days=29)
    assert captured["group_by"] == "day"


def test_summary_rejects_unknown_dimension(client, monkeypatch) -> None:
    _patch_summary(monkeypatch, {})
    resp = client.get("/admin/usage/summary", params={"group_by": "week"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


def test_summary_rejects_range_over_limit(client, monkeypatch) -> None:
    _patch_summary(monkeypatch, {})
    resp = client.get("/admin/usage/summary", params={"from": "2000-01-01", "to": "2026-01-01"})
    assert resp.status_code == 400
    assert str(settings.usage_query_max_days) in resp.json()["detail"]["message"]


def test_summary_rejects_reversed_range(client, monkeypatch) -> None:
    _patch_summary(monkeypatch, {})
    resp = client.get("/admin/usage/summary", params={"from": "2026-10-02", "to": "2026-10-01"})
    assert resp.status_code == 400


def test_export_csv_is_bom_encoded(client, monkeypatch) -> None:
    async def fake_rows(**kw):
        assert kw["date_from"] == date(2026, 10, 1)
        return SAMPLE_ROWS

    monkeypatch.setattr(au.UsageRepo, "export_rows", fake_rows)

    resp = client.get("/admin/usage/export.csv", params={"from": "2026-10-01", "to": "2026-10-01"})
    # Excel 只认 BOM 才不乱码
    assert resp.content.startswith(b"\xef\xbb\xbf")
    text = resp.content.decode("utf-8-sig")
    assert "日期" in text and "Token（估算）" in text
    assert "未归属" in text and "900" in text
    assert 'filename="usage_' in resp.headers["content-disposition"]


def test_usage_endpoints_require_admin(make_test_app) -> None:
    app = make_test_app([au.router], prefix="")
    as_agent = TestClient(make_test_app.as_role(app, "agent"))
    for path in ("/admin/usage/summary", "/admin/usage/export.csv"):
        resp = as_agent.get(path)
        assert resp.status_code == 403, path
        assert resp.json()["detail"]["code"] == 2003
