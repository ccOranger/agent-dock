"""管理后台咨询接口测试：跨客户列表/过滤、会话详情、CSV 导出、RBAC（Repo 层 mock，不依赖真实 DB）"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import Forbidden
from app.api.v1 import admin_inquiries as ai
from app.core.exceptions import ParamError

SAMPLE_ROWS = [
    {
        "id": "s-1",
        "title": "订单查询",
        "status": "closed",
        "last_route_to": "order_service",
        "user_id": "u-c1",
        "username": "alice",
        "display_name": "爱丽丝",
        "message_count": 3,
        "created_at": datetime(2025, 1, 1, 10, 0, 0),
        "updated_at": datetime(2025, 1, 1, 10, 5, 0),
    },
    {
        "id": "s-2",
        "title": "退货政策",
        "status": "active",
        "last_route_to": "knowledge_qa",
        "user_id": "u-c2",
        "username": "bob",
        "display_name": "鲍勃",
        "message_count": 1,
        "created_at": datetime(2025, 1, 2, 9, 0, 0),
        "updated_at": datetime(2025, 1, 2, 9, 1, 0),
    },
]

SAMPLE_EXPORT_ROWS = [
    {
        "session_id": "s-1",
        "customer": "爱丽丝",
        "username": "alice",
        "title": "订单查询",
        "status": "closed",
        "last_route_to": "order_service",
        "created_at": "2025-01-01T10:00:00",
        "excerpt": "我的订单到哪了",
    }
]


@pytest.fixture
def client(make_test_app):
    app = make_test_app([ai.router], prefix="")
    return TestClient(make_test_app.as_role(app, "agent"))


def _capture_list(monkeypatch, captured: dict):
    async def fake_list_all_filtered(*, page, page_size, **filters):
        captured["page"] = page
        captured["page_size"] = page_size
        captured.update(filters)
        return SAMPLE_ROWS, 42

    async def fake_scenes():
        return []

    monkeypatch.setattr(ai.SessionRepo, "list_all_filtered", fake_list_all_filtered)
    monkeypatch.setattr(ai.SceneRepo, "list_all", fake_scenes)


def test_list_sessions_pagination_and_filters_passthrough(client, monkeypatch):
    captured: dict = {}
    _capture_list(monkeypatch, captured)

    resp = client.get(
        f"{ai.router.prefix}/sessions",
        params={
            "page": 2,
            "page_size": 10,
            "status": "closed",
            "last_route_to": "order_service",
            "user_id": "u-c1",
            "date_from": "2025-01-01T00:00:00",
            "date_to": "2025-01-31T23:59:59",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["total"] == 42
    assert body["data"]["page"] == 2
    assert len(body["data"]["items"]) == 2
    assert body["data"]["items"][0]["display_name"] == "爱丽丝"
    # 过滤条件正确透传到 Repo 层，且时间已解析为 datetime
    assert captured["status"] == "closed"
    assert captured["last_route_to"] == "order_service"
    assert captured["user_id"] == "u-c1"
    assert captured["date_from"] == datetime(2025, 1, 1, 0, 0, 0)
    assert captured["date_to"] == datetime(2025, 1, 31, 23, 59, 59)


def test_list_sessions_invalid_date_returns_400(client):
    resp = client.get(f"{ai.router.prefix}/sessions", params={"date_from": "not-a-date"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


def test_parse_dt_empty_and_invalid():
    assert ai._parse_dt(None) is None
    assert ai._parse_dt("") is None
    assert ai._parse_dt("2025-01-01T12:00:00") == datetime(2025, 1, 1, 12, 0, 0)
    with pytest.raises(ParamError):
        ai._parse_dt("2025年1月1日")


def test_export_csv_header_bom_and_rows(client, monkeypatch):
    captured: dict = {}

    async def fake_export_rows(**filters):
        captured.update(filters)
        return SAMPLE_EXPORT_ROWS

    monkeypatch.setattr(ai.SessionRepo, "export_rows", fake_export_rows)

    async def fake_scenes():
        return []

    monkeypatch.setattr(ai.SceneRepo, "list_all", fake_scenes)

    resp = client.get(f"{ai.router.prefix}/sessions/export", params={"status": "closed"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert captured["status"] == "closed"

    raw = resp.content
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM，Excel 友好
    text = raw.decode("utf-8-sig")
    header_line = text.splitlines()[0]
    for col in ["会话ID", "客户", "用户名", "状态", "最后路由", "首条问句"]:
        assert col in header_line
    assert "我的订单到哪了" in text


def test_get_inquiry_detail_with_messages(client, monkeypatch):
    sess = SimpleNamespace(
        id="s-1",
        title="订单查询",
        status="closed",
        scene_id="scene-1",
        last_route_to="order_service",
        user_id="u-c1",
        created_at=datetime(2025, 1, 1, 10, 0, 0),
        updated_at=datetime(2025, 1, 1, 10, 5, 0),
    )
    messages = [{"id": "m-1", "role": "user", "content": "我的订单到哪了"}]

    async def fake_get(session_id):
        assert session_id == "s-1"
        return sess

    async def fake_scene_get(scene_id):
        return SimpleNamespace(code="mall_order")

    async def fake_list_by_session(session_id):
        return messages

    monkeypatch.setattr(ai.SessionRepo, "get", fake_get)
    monkeypatch.setattr(ai.SceneRepo, "get", fake_scene_get)
    monkeypatch.setattr(ai.MessageRepo, "list_by_session", fake_list_by_session)

    resp = client.get(f"{ai.router.prefix}/sessions/s-1")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["session"]["id"] == "s-1"
    assert data["session"]["user_id"] == "u-c1"
    assert data["messages"] == messages


def test_get_inquiry_not_found(client, monkeypatch):
    async def fake_get(session_id):
        return None

    monkeypatch.setattr(ai.SessionRepo, "get", fake_get)

    resp = client.get(f"{ai.router.prefix}/sessions/missing")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == 3001


def test_customer_forbidden_on_all_inquiry_endpoints(make_test_app):
    app = make_test_app([ai.router], prefix="")
    customer_client = TestClient(make_test_app.as_role(app, "customer"))
    paths = [
        f"{ai.router.prefix}/sessions",
        f"{ai.router.prefix}/sessions/export",
        f"{ai.router.prefix}/sessions/s-1",
    ]
    for path in paths:
        resp = customer_client.get(path)
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == 2003


def test_unauthenticated_returns_401(make_test_app):
    app = make_test_app([ai.router], prefix="")
    anon = TestClient(app)
    resp = anon.get(f"{ai.router.prefix}/sessions")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == 2001


def test_require_staff_accepts_agent_and_admin():
    import asyncio

    from app.api.deps import require_staff

    # 直接传入模拟解析后的 user，验证角色判定
    dep = require_staff

    async def _resolve(role):
        return await dep(user=SimpleNamespace(role=role))

    asyncio.run(_resolve("agent"))
    asyncio.run(_resolve("admin"))
    with pytest.raises(Forbidden):
        asyncio.run(_resolve("customer"))
