"""客户侧接管轮询 GET /chat/poll：归属校验（登录客户/匿名访客）、增量游标、非法入参。

Repo 与场景解析全部 mock，不触真实 DB / 模型。
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.api.v1.chat as chat
from app.api.deps import optional_customer
from app.models.repositories import MessageRepo, SessionRepo, VisitorRepo
from app.schemas.scene import DEFAULT_SCENE_ID, SceneContext

SESSION_ID = str(uuid.uuid4())
CUSTOMER_ID = "u-1"
VISITOR_EXTERNAL = "v_abc"
VISITOR_INTERNAL = str(uuid.uuid4())
MSG_ID = str(uuid.uuid4())

_SCENE = SceneContext(
    scene_id=DEFAULT_SCENE_ID,
    code="default",
    is_default=True,
    router_mode="direct",
    agent_names=("default_assistant",),
    direct_agent="default_assistant",
)


@pytest.fixture
def client(make_test_app, monkeypatch):
    app = make_test_app([chat.router], prefix="/api/v1")
    # 默认当作已登录客户；访客用例各自覆盖
    app.dependency_overrides[optional_customer] = lambda: SimpleNamespace(
        id=CUSTOMER_ID, role="customer", is_active=True
    )
    monkeypatch.setattr(chat, "resolve_scene", _async_ret(_SCENE))
    monkeypatch.setattr(chat, "authorize_scene_access", _noop)
    return TestClient(app)


def _async_ret(value):
    async def _f(*a, **kw):
        return value

    return _f


def _noop(*a, **kw):
    return None


def _session(status="human_handling", *, user_id=CUSTOMER_ID, visitor_id=None):
    return SimpleNamespace(
        id=SESSION_ID,
        scene_id=DEFAULT_SCENE_ID,
        user_id=user_id,
        visitor_id=visitor_id,
        status=status,
    )


def test_customer_poll_returns_messages_and_cursor(client, monkeypatch):
    seen: dict = {}

    async def fake_get(session_id, db=None):
        seen["get"] = session_id
        return _session()

    async def fake_after(session_id, after_id=None):
        seen["after_id"] = after_id
        return [{"id": MSG_ID, "role": "assistant", "content": "您好，人工客服", "sender_type": "staff"}]

    monkeypatch.setattr(SessionRepo, "get", fake_get)
    monkeypatch.setattr(MessageRepo, "list_by_session_after", fake_after)

    resp = client.get(f"/api/v1/chat/poll?session_id={SESSION_ID}&after_id={MSG_ID}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "human_handling"
    assert data["messages"][0]["sender_type"] == "staff"
    assert seen["after_id"] == MSG_ID
    assert str(seen["get"]) == SESSION_ID


def test_customer_poll_wrong_owner_is_404(client, monkeypatch):
    async def fake_get(session_id, db=None):
        # 会话属于别的客户
        return _session(user_id=str(uuid.uuid4()))

    monkeypatch.setattr(SessionRepo, "get", fake_get)
    resp = client.get(f"/api/v1/chat/poll?session_id={SESSION_ID}")
    assert resp.status_code == 404


def test_missing_identity_is_401(make_test_app, monkeypatch):
    app = make_test_app([chat.router], prefix="/api/v1")
    # 不覆盖 optional_customer：无 Authorization → user=None，且不带 visitor_id
    monkeypatch.setattr(chat, "resolve_scene", _async_ret(_SCENE))
    monkeypatch.setattr(chat, "authorize_scene_access", _noop)
    anon = TestClient(app)
    resp = anon.get(f"/api/v1/chat/poll?session_id={SESSION_ID}")
    assert resp.status_code == 401


def test_invalid_session_id_is_400(client):
    resp = client.get("/api/v1/chat/poll?session_id=not-a-uuid")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


def test_empty_session_id_is_404(client):
    resp = client.get("/api/v1/chat/poll?session_id=")
    assert resp.status_code == 404


def test_anonymous_visitor_ownership_resolved(make_test_app, monkeypatch):
    """无登录态但带 visitor_id：按外部标识解析内部访客行并校验归属。"""
    app = make_test_app([chat.router], prefix="/api/v1")
    monkeypatch.setattr(chat, "resolve_scene", _async_ret(_SCENE))
    monkeypatch.setattr(chat, "authorize_scene_access", _noop)

    captured: dict = {}

    async def fake_visitor(scene_id, external_key=None, integration_id=None):
        captured["external_key"] = external_key
        return SimpleNamespace(id=VISITOR_INTERNAL)

    async def fake_get(session_id, db=None):
        # 会话归属该内部访客
        return _session(user_id=None, visitor_id=VISITOR_INTERNAL)

    async def fake_after(session_id, after_id=None):
        return []

    monkeypatch.setattr(VisitorRepo, "get_or_create", fake_visitor)
    monkeypatch.setattr(SessionRepo, "get", fake_get)
    monkeypatch.setattr(MessageRepo, "list_by_session_after", fake_after)

    anon = TestClient(app)
    resp = anon.get(f"/api/v1/chat/poll?session_id={SESSION_ID}&visitor_id={VISITOR_EXTERNAL}")
    assert resp.status_code == 200
    assert captured["external_key"] == VISITOR_EXTERNAL
    assert resp.json()["data"]["messages"] == []

    # 访客不匹配的会话 → 404
    async def fake_get_other(session_id, db=None):
        return _session(user_id=None, visitor_id=str(uuid.uuid4()))

    monkeypatch.setattr(SessionRepo, "get", fake_get_other)
    assert anon.get(f"/api/v1/chat/poll?session_id={SESSION_ID}&visitor_id={VISITOR_EXTERNAL}").status_code == 404
