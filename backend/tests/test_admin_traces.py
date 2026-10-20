"""管理后台链路接口测试：多维过滤透传、三种 trace_ref 查详情、用量聚合、RBAC（Repo 全 mock）"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import admin_traces as at

SAMPLE_ROW = {
    "id": "55555555-5555-5555-5555-555555555555",
    "trace_id": "abcd1234abcd1234",
    "session_id": "11111111-1111-1111-1111-111111111111",
    "user_id": "22222222-2222-2222-2222-222222222222",
    "assistant_message_id": "44444444-4444-4444-4444-444444444444",
    "intent_count": 2,
    "agent_names": ["order_service", "knowledge_qa"],
    "agent_sources": ["code", "code"],
    "llm_models": ["deepseek-chat"],
    "mcp_server_names": ["erp-mcp"],
    "tool_names": ["order_query"],
    "rag_namespaces": ["cs_default"],
    "memory_hit_count": 1,
    "rag_chunk_count": 3,
    "tool_call_count": 1,
    "tool_error_count": 0,
    "confidence": 0.91,
    "outcome": "answered",
    "handover_reason": None,
    "error_code": None,
    "error_message": None,
    "ttft_ms": 820,
    "total_latency_ms": 3100,
    "response_chars": 240,
    "span_count": 7,
    "created_at": "2026-10-14T10:00:00+00:00",
}


@pytest.fixture
def client(make_test_app):
    app = make_test_app([at.router], prefix="")
    return TestClient(make_test_app.as_role(app, "agent"))


def _capture_list(monkeypatch, captured: dict):
    async def fake_list_filtered(**kw):
        captured.update(kw)
        return [SAMPLE_ROW], 12

    monkeypatch.setattr(at.TraceRepo, "list_filtered", fake_list_filtered)


def test_list_passthrough_all_filters(client, monkeypatch):
    captured: dict = {}
    _capture_list(monkeypatch, captured)

    resp = client.get(
        f"{at.router.prefix}",
        params={
            "page": 3,
            "page_size": 50,
            "session_id": SAMPLE_ROW["session_id"],
            "user_id": SAMPLE_ROW["user_id"],
            "agent_name": "order_service",
            "tool_name": "order_query",
            "mcp_server": "erp-mcp",
            "outcome": "answered",
            "has_error": True,
            "min_latency_ms": 2000,
            "date_from": "2026-10-01T00:00:00",
            "date_to": "2026-10-14T23:59:59",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["total"] == 12
    assert body["data"]["items"][0]["trace_id"] == "abcd1234abcd1234"
    assert captured["page"] == 3 and captured["page_size"] == 50
    assert captured["agent_name"] == "order_service"
    assert captured["tool_name"] == "order_query"
    assert captured["mcp_server"] == "erp-mcp"
    assert captured["outcome"] == "answered"
    assert captured["has_error"] is True
    assert captured["min_latency_ms"] == 2000
    # 时间串已解析为 datetime 再交给 Repo
    assert captured["date_from"].year == 2026 and captured["date_from"].month == 10
    assert captured["date_to"].hour == 23


def test_list_defaults_and_empty_optional_filters(client, monkeypatch):
    captured: dict = {}
    _capture_list(monkeypatch, captured)
    resp = client.get(at.router.prefix)
    assert resp.status_code == 200
    assert captured["page"] == 1 and captured["page_size"] == 20
    assert captured["has_error"] is False
    assert captured["agent_name"] is None and captured["date_from"] is None


def test_list_invalid_date_returns_400(client):
    resp = client.get(at.router.prefix, params={"date_from": "2026年10月1日"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


def test_usage_route_not_swallowed_by_detail(client, monkeypatch):
    """/usage 必须命中用量聚合，而不是被 /{trace_ref} 当 ID 吃掉。"""

    async def fake_usage(**kw):
        captured.append(kw)
        return [{"name": "order_query", "count": 9}]

    async def fake_detail(trace_ref):  # 命中即说明路由顺序有问题
        raise AssertionError(f"detail should not be called, got {trace_ref}")

    captured: list = []
    monkeypatch.setattr(at.TraceRepo, "usage_counts", fake_usage)
    monkeypatch.setattr(at.TraceRepo, "detail", fake_detail)

    resp = client.get(f"{at.router.prefix}/usage", params={"field": "mcp_server_names", "days": 30})
    assert resp.status_code == 200
    assert resp.json()["data"] == [{"name": "order_query", "count": 9}]
    assert captured == [{"field": "mcp_server_names", "days": 30}]


def test_usage_rejects_unknown_field(client):
    resp = client.get(f"{at.router.prefix}/usage", params={"field": "spans"})
    assert resp.status_code == 422


def test_session_traces(client, monkeypatch):
    seen: dict = {}

    async def fake_list_by_session(session_id):
        seen["session_id"] = session_id
        return [SAMPLE_ROW]

    monkeypatch.setattr(at.TraceRepo, "list_by_session", fake_list_by_session)
    resp = client.get(f"{at.router.prefix}/sessions/{SAMPLE_ROW['session_id']}")
    assert resp.status_code == 200
    assert seen["session_id"] == SAMPLE_ROW["session_id"]
    assert resp.json()["data"][0]["span_count"] == 7


@pytest.mark.asyncio
async def test_detail_accepts_three_reference_kinds(monkeypatch):
    for ref in [SAMPLE_ROW["id"], "abcd1234abcd1234", SAMPLE_ROW["assistant_message_id"]]:

        async def fake_detail(trace_ref, expected=ref):
            assert trace_ref == expected
            return {**SAMPLE_ROW, "spans": [{"type": "tool.call", "name": "order_query"}]}

        monkeypatch.setattr(at.TraceRepo, "detail", fake_detail)
        data = await at.get_trace(ref, user=SimpleNamespace(role="admin"))
        assert data.data["span_count"] == 7
        assert len(data.data["spans"]) == 1


@pytest.mark.asyncio
async def test_detail_missing_raises_404(monkeypatch):
    from app.core.exceptions import NotFound

    async def fake_detail(trace_ref):
        return None

    monkeypatch.setattr(at.TraceRepo, "detail", fake_detail)
    with pytest.raises(NotFound):
        await at.get_trace("nope", user=SimpleNamespace(role="admin"))


def test_detail_endpoint_404(client, monkeypatch):
    async def fake_detail(trace_ref):
        return None

    monkeypatch.setattr(at.TraceRepo, "detail", fake_detail)
    resp = client.get(f"{at.router.prefix}/unknown-ref")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == 3001


def test_customer_forbidden_on_all_trace_endpoints(make_test_app):
    app = make_test_app([at.router], prefix="")
    customer_client = TestClient(make_test_app.as_role(app, "customer"))
    paths = [
        at.router.prefix,
        f"{at.router.prefix}/usage",
        f"{at.router.prefix}/sessions/s-1",
        f"{at.router.prefix}/t-1",
    ]
    for path in paths:
        resp = customer_client.get(path)
        assert resp.status_code == 403, path
        assert resp.json()["detail"]["code"] == 2003


def test_unauthenticated_returns_401(make_test_app):
    app = make_test_app([at.router], prefix="")
    resp = TestClient(app).get(at.router.prefix)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == 2001
