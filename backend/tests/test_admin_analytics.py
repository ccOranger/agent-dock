"""数据分析（成效指标）测试：六个比率的分子分母、空数据、采样 meta、RBAC 与菜单登记。

全部 mock 仓储，不连真库也不打真实 LLM：这里锁的是「口径与守卫」，不是 SQL 执行结果。
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import admin_analytics as aa
from app.api.v1 import admin_rbac as rbac_api
from app.core.config import settings
from app.core.rbac import DEFAULT_AGENT_GRANTS, MENU_KEYS
from app.models.repositories import TraceRepo, UsageRepo

PATH = "/admin/analytics/outcomes"


@pytest.fixture
def client(make_test_app):
    app = make_test_app([aa.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


def _usage_totals(*, requests=10, handoffs=2, errors=1):
    return {
        "requests": requests,
        "tokens": 0,
        "estimated_requests": 0,
        "handoffs": handoffs,
        "errors": errors,
    }


def _trace(
    *, traces=8, tool_calls=20, tool_errors=3, fallbacks=2, rag_hits=5
):
    return {
        "traces": traces,
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "fallbacks": fallbacks,
        "rag_hits": rag_hits,
    }


def _patch(monkeypatch, *, usage=None, trace=None, rows=None, captured: dict | None = None):
    usage_totals = _usage_totals() if usage is None else usage
    trace_counts = _trace() if trace is None else trace
    daily_rows = [] if rows is None else rows

    async def fake_summary(**kw):
        if captured is not None:
            captured["usage"] = kw
        return {"rows": daily_rows, "totals": usage_totals}

    async def fake_outcome(**kw):
        if captured is not None:
            captured["trace"] = kw
        return trace_counts

    monkeypatch.setattr(UsageRepo, "summary", fake_summary)
    monkeypatch.setattr(TraceRepo, "outcome_summary", fake_outcome)


def _rate(data: dict, key: str) -> dict:
    return data[key]


# --------------------------------------------------------------- 六个比率
def test_outcomes_computes_six_rates(client, monkeypatch) -> None:
    _patch(monkeypatch)

    data = client.get(PATH).json()["data"]

    # 全量三率：转人工 2/10、解决 (10-2-1)/10、错误 1/10
    assert _rate(data, "handover_rate") == {"value": 0.2, "numerator": 2, "denominator": 10}
    assert _rate(data, "resolution_rate") == {"value": 0.7, "numerator": 7, "denominator": 10}
    assert _rate(data, "error_rate") == {"value": 0.1, "numerator": 1, "denominator": 10}

    # 链路三率：工具成功 (20-3)/20、兜底 2/8、RAG 命中 5/8
    assert _rate(data, "tool_success_rate") == {
        "value": 0.85,
        "numerator": 17,
        "denominator": 20,
    }
    assert _rate(data, "fallback_rate") == {"value": 0.25, "numerator": 2, "denominator": 8}
    assert _rate(data, "rag_hit_rate") == {"value": 0.625, "numerator": 5, "denominator": 8}


def test_outcomes_resolution_never_negative(client, monkeypatch) -> None:
    """脏数据（handoffs+errors > requests）不允许出现负的解决率。"""
    _patch(monkeypatch, usage=_usage_totals(requests=1, handoffs=1, errors=1))

    data = client.get(PATH).json()["data"]

    assert _rate(data, "resolution_rate") == {"value": 0.0, "numerator": 0, "denominator": 1}


# --------------------------------------------------------------- 空数据
def test_outcomes_empty_data_returns_null_values(client, monkeypatch) -> None:
    _patch(
        monkeypatch,
        usage=_usage_totals(requests=0, handoffs=0, errors=0),
        trace=_trace(traces=0, tool_calls=0, tool_errors=0, fallbacks=0, rag_hits=0),
    )

    resp = client.get(PATH)
    assert resp.status_code == 200
    data = resp.json()["data"]

    # 分母为 0 一律 null：前端显示"—"，不把"没有数据"伪装成 0%
    for key in (
        "handover_rate",
        "resolution_rate",
        "error_rate",
        "tool_success_rate",
        "fallback_rate",
        "rag_hit_rate",
    ):
        assert _rate(data, key) == {"value": None, "numerator": 0, "denominator": 0}, key

    assert data["meta"]["requests"] == 0
    assert data["meta"]["traces_covered"] == 0
    assert data["daily"] == []


# --------------------------------------------------------------- 每日统计
def test_outcomes_daily_rows_drop_tokens(client, monkeypatch) -> None:
    """每日统计接着用量报表的日维度：只给请求/转人工/错误，token 是估算值不对外输出。"""
    rows = [
        {
            "day": "2026-10-01",
            "requests": 5,
            "tokens": 999,
            "estimated_requests": 5,
            "handoffs": 1,
            "errors": 0,
        },
        {
            "day": "2026-10-02",
            "requests": 3,
            "tokens": 100,
            "estimated_requests": 3,
            "handoffs": 0,
            "errors": 1,
        },
    ]
    _patch(monkeypatch, rows=rows)

    daily = client.get(PATH).json()["data"]["daily"]

    assert daily == [
        {"day": "2026-10-01", "requests": 5, "handoffs": 1, "errors": 0},
        {"day": "2026-10-02", "requests": 3, "handoffs": 0, "errors": 1},
    ]
    # 字段白名单：以后即使仓储多返回列，也不会把 token 泄到前端
    assert all("tokens" not in row for row in daily)


# --------------------------------------------------------------- 参数与 meta
def test_outcomes_defaults_to_last_7_days(client, monkeypatch) -> None:
    captured: dict = {}
    _patch(monkeypatch, captured=captured)

    client.get(PATH)

    today = datetime.now().astimezone().date()
    assert captured["usage"]["date_from"] == today - timedelta(days=6)
    assert captured["usage"]["date_to"] == today
    assert captured["usage"]["group_by"] == "day"
    # 链路过滤用本地日边界（aware datetime），与日聚合同一时间窗
    assert captured["trace"]["date_from"].tzinfo is not None
    assert captured["trace"]["date_from"].date() == today - timedelta(days=6)
    assert captured["trace"]["date_to"].date() == today


def test_outcomes_passes_scene_filter(client, monkeypatch) -> None:
    captured: dict = {}
    _patch(monkeypatch, captured=captured)

    client.get(PATH, params={"scene_code": "cs_default"})

    assert captured["usage"]["scene_code"] == "cs_default"
    assert captured["trace"]["scene_code"] == "cs_default"


def test_outcomes_meta_reports_sampling(client, monkeypatch) -> None:
    _patch(monkeypatch)
    monkeypatch.setattr(settings, "trace_sample_rate", 0.2)

    meta = client.get(PATH).json()["data"]["meta"]

    assert meta["full_source"] == "chat_usage_daily"
    assert meta["trace_source"] == "request_traces"
    assert meta["trace_sample_rate"] == 0.2
    # 采样率 < 1 时前端必须挂"采样口径"提示
    assert meta["sampled"] is True
    assert meta["requests"] == 10
    assert meta["traces_covered"] == 8


def test_outcomes_full_sampling_is_not_marked(client, monkeypatch) -> None:
    _patch(monkeypatch)
    monkeypatch.setattr(settings, "trace_sample_rate", 1.0)

    meta = client.get(PATH).json()["data"]["meta"]
    assert meta["sampled"] is False


def test_outcomes_rejects_bad_date(client, monkeypatch) -> None:
    _patch(monkeypatch)
    resp = client.get(PATH, params={"from": "2026/10/01"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


def test_outcomes_rejects_range_over_limit(client, monkeypatch) -> None:
    _patch(monkeypatch)
    resp = client.get(PATH, params={"from": "2000-01-01", "to": "2026-01-01"})
    assert resp.status_code == 400
    assert str(settings.usage_query_max_days) in resp.json()["detail"]["message"]


# --------------------------------------------------------------- RBAC 与菜单
def test_outcomes_denied_without_analytics_grant(make_test_app, monkeypatch) -> None:
    """conftest 的 autouse 桩把有效权限置空：坐席默认无 analytics → 403。"""
    _patch(monkeypatch)
    app = make_test_app([aa.router], prefix="")
    as_agent = TestClient(make_test_app.as_role(app, "agent"))

    resp = as_agent.get(PATH)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 2003


def test_outcomes_allowed_with_analytics_read(make_test_app, monkeypatch) -> None:
    from app.models.repositories import RoleRepo

    _patch(monkeypatch)

    async def grants(user_id):
        return {"analytics": "read"}

    monkeypatch.setattr(RoleRepo, "resolve_permissions", grants)
    app = make_test_app([aa.router], prefix="")
    as_agent = TestClient(make_test_app.as_role(app, "agent"))

    assert as_agent.get(PATH).status_code == 200


def test_analytics_menu_registered_and_not_default_for_agent(make_test_app) -> None:
    assert "analytics" in MENU_KEYS
    # 用量报表页已并入本页：菜单 key 不再单独存在
    assert "usage" not in MENU_KEYS
    # 与 dashboard 一致：坐席默认不授予，需管理员在角色矩阵里显式打开
    assert "analytics" not in DEFAULT_AGENT_GRANTS

    app = make_test_app([rbac_api.router], prefix="")
    as_admin = TestClient(make_test_app.as_role(app, "admin"))
    modules = {m["key"]: m for m in as_admin.get("/admin/rbac/menus").json()["data"]}

    assert modules["analytics"]["name"] == "数据分析"
    assert modules["analytics"]["group"] == "观测"
