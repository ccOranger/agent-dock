"""M2 多场景：场景解析、直连分发、访客 actor 与后台只读保护（Repo/DB mock）。"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents import orchestrator as orch_mod
from app.agents.orchestrator import Orchestrator
from app.api.v1 import admin_scenes as scenes_api
from app.core.exceptions import SceneDisabled, SceneNotConfigured, SceneNotFound
from app.models.repositories import AgentRepo, AuditRepo, SceneRepo, VisitorRepo
from app.schemas.scene import (
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_SCENE_CODE,
    DEFAULT_SCENE_ID,
    ActorType,
    SceneContext,
)
from app.services.chat_service import ChatService
from app.services.scene_service import resolve_scene

ADMIN_PATH = scenes_api.router.prefix


def _agent(name: str, agent_id: str | None = None) -> dict:
    return {"id": agent_id or str(uuid.uuid4()), "name": name, "display_name": name}


def _scene(**kw) -> SimpleNamespace:
    base = {
        "id": uuid.UUID(DEFAULT_SCENE_ID),
        "code": DEFAULT_SCENE_CODE,
        "display_name": "默认场景",
        "description": None,
        "status": "active",
        "is_default": True,
        "router_mode": "direct",
        "router_model": None,
        "router_confidence_threshold": None,
        "router_max_intents": None,
        "fallback_agent_id": None,
        "fallback_text": None,
        "extra_router_prompt": None,
        "publish_key": None,
        "allowed_origins": [],
        "created_at": None,
        "updated_at": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _install_scene(monkeypatch, scene, agents) -> None:
    async def get_by_code(code):
        return scene if scene is not None and scene.code == code else None

    async def list_bound_agents(scene_id):
        return list(agents)

    monkeypatch.setattr(SceneRepo, "get_by_code", get_by_code)
    monkeypatch.setattr(SceneRepo, "list_bound_agents", list_bound_agents)


# ===== resolve_scene =====
@pytest.mark.asyncio
async def test_no_code_falls_back_to_default_scene_direct(monkeypatch):
    _install_scene(monkeypatch, _scene(), [_agent("default_assistant")])
    ctx = await resolve_scene(None)
    assert ctx.code == DEFAULT_SCENE_CODE
    assert ctx.is_default is True
    assert ctx.direct_agent == "default_assistant"


@pytest.mark.asyncio
async def test_unknown_scene_raises_3003(monkeypatch):
    _install_scene(monkeypatch, _scene(), [_agent("default_assistant")])
    with pytest.raises(SceneNotFound):
        await resolve_scene("nope")


@pytest.mark.asyncio
async def test_paused_scene_raises_3004(monkeypatch):
    scene = _scene(code="sales", is_default=False, status="paused")
    _install_scene(monkeypatch, scene, [_agent("sales_agent")])
    with pytest.raises(SceneDisabled):
        await resolve_scene("sales")


@pytest.mark.asyncio
async def test_scene_without_agents_raises_3005(monkeypatch):
    scene = _scene(code="sales", is_default=False, router_mode="auto")
    _install_scene(monkeypatch, scene, [])
    with pytest.raises(SceneNotConfigured):
        await resolve_scene("sales")


@pytest.mark.asyncio
async def test_auto_mode_single_agent_is_direct(monkeypatch):
    scene = _scene(code="sales", is_default=False, router_mode="auto")
    _install_scene(monkeypatch, scene, [_agent("sales_agent")])
    ctx = await resolve_scene("sales")
    assert ctx.direct_agent == "sales_agent"


@pytest.mark.asyncio
async def test_auto_mode_multi_agents_uses_router(monkeypatch):
    scene = _scene(code="sales", is_default=False, router_mode="auto")
    _install_scene(monkeypatch, scene, [_agent("a"), _agent("b")])
    ctx = await resolve_scene("sales")
    assert ctx.agent_names == ("a", "b")
    assert ctx.direct_agent is None


@pytest.mark.asyncio
async def test_llm_mode_never_direct(monkeypatch):
    scene = _scene(code="sales", is_default=False, router_mode="llm")
    _install_scene(monkeypatch, scene, [_agent("only_one")])
    ctx = await resolve_scene("sales")
    assert ctx.direct_agent is None


@pytest.mark.asyncio
async def test_direct_mode_prefers_fallback_agent(monkeypatch):
    target = str(uuid.uuid4())
    scene = _scene(
        code="sales", is_default=False, router_mode="direct", fallback_agent_id=uuid.UUID(target)
    )
    _install_scene(monkeypatch, scene, [_agent("a"), _agent("b", target)])
    ctx = await resolve_scene("sales")
    assert ctx.direct_agent == "b"


# ===== Orchestrator 直连 =====
class _BoomRouter:
    async def route(self, *args, **kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("直连场景不应调用路由 LLM")


class _FakeCtx:
    def __init__(self):
        self.persisted = []

    async def build(self, user_id, query, rag_namespaces, collector=None):
        return query

    async def persist_after_run(self, **kwargs):
        self.persisted.append(kwargs)


async def _no_canned(query, scene_id=None):
    return None


@pytest.mark.asyncio
async def test_dispatch_direct_scene_skips_router(monkeypatch):
    orch = Orchestrator()
    orch.router = _BoomRouter()
    orch.ctx_engineer = _FakeCtx()

    async def fake_run(business_agent, user_id, query, **kwargs):
        yield {"type": "text", "data": {"content": f"<{business_agent.name}>"}}

    orch._run_business_agent = fake_run

    async def fake_resolve(name, user_id, session_id=None):
        return SimpleNamespace(name=name, mcp_tools=[], rag_namespace=None)

    monkeypatch.setattr(orch_mod, "resolve_business_agent", fake_resolve)
    monkeypatch.setattr(orch_mod, "match_canned_script", _no_canned)

    scene = SceneContext(
        scene_id=DEFAULT_SCENE_ID,
        code=DEFAULT_SCENE_CODE,
        is_default=True,
        router_mode="direct",
        agent_names=("default_assistant",),
        direct_agent="default_assistant",
    )
    events = [
        e
        async for e in orch.dispatch(
            user_id="u1", session_id="s1", query="你好", history=[], scene=scene
        )
    ]
    route = next(e for e in events if e["type"] == "route")
    assert route["data"]["agent_name"] == "default_assistant"
    assert route["data"]["router_skipped"] is True
    assert route["data"]["scene_code"] == DEFAULT_SCENE_CODE
    assert any(
        e["type"] == "text" and e["data"]["content"] == "<default_assistant>" for e in events
    )


# ===== 后台默认场景保护 =====
@pytest.fixture
def admin_client(make_test_app):
    app = make_test_app([scenes_api.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


def _install_admin_scene(monkeypatch, scene, agents=None) -> None:
    async def get(scene_id):
        return scene

    async def list_bound_agents(scene_id):
        return list(agents or [])

    async def get_bound_agent_ids(scene_id):
        return []

    async def update(scene_id, **fields):
        for key, value in fields.items():
            setattr(scene, key, value)
        return scene

    async def write(*args, **kwargs):
        return None

    monkeypatch.setattr(SceneRepo, "get", get)
    monkeypatch.setattr(SceneRepo, "list_bound_agents", list_bound_agents)
    monkeypatch.setattr(SceneRepo, "get_bound_agent_ids", get_bound_agent_ids)
    monkeypatch.setattr(SceneRepo, "update", update)
    monkeypatch.setattr(AuditRepo, "write", write)


def test_default_scene_rejects_non_display_update(admin_client, monkeypatch):
    _install_admin_scene(monkeypatch, _scene())
    resp = admin_client.patch(f"{ADMIN_PATH}/{DEFAULT_SCENE_ID}", json={"router_mode": "llm"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


def test_default_scene_allows_display_update(admin_client, monkeypatch):
    _install_admin_scene(monkeypatch, _scene())
    resp = admin_client.patch(
        f"{ADMIN_PATH}/{DEFAULT_SCENE_ID}", json={"display_name": "默认入口"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["display_name"] == "默认入口"


def test_default_scene_rejects_agent_rebind(admin_client, monkeypatch):
    _install_admin_scene(monkeypatch, _scene())
    resp = admin_client.put(f"{ADMIN_PATH}/{DEFAULT_SCENE_ID}/agents", json={"agent_ids": []})
    assert resp.status_code == 400


def test_default_scene_cannot_be_deleted(admin_client, monkeypatch):
    _install_admin_scene(monkeypatch, _scene())
    resp = admin_client.delete(f"{ADMIN_PATH}/{DEFAULT_SCENE_ID}")
    assert resp.status_code == 400


def test_create_scene_rejects_reserved_code(admin_client, monkeypatch):
    async def get_by_code(code):
        return None

    monkeypatch.setattr(SceneRepo, "get_by_code", get_by_code)
    resp = admin_client.post(
        ADMIN_PATH, json={"code": DEFAULT_SCENE_CODE, "display_name": "冒牌默认"}
    )
    assert resp.status_code == 409


# ===== 默认 Agent 不可停用 =====
def test_default_assistant_cannot_be_disabled(make_test_app, monkeypatch):
    from app.api.v1 import admin as admin_api

    async def get_by_name(name):
        return SimpleNamespace(name=name, source="code")

    async def set_enabled(name, enabled):
        raise AssertionError("停用拦截生效时不应写库")

    monkeypatch.setattr(AgentRepo, "get_by_name", get_by_name)
    monkeypatch.setattr(AgentRepo, "set_enabled", set_enabled)
    app = make_test_app([admin_api.router], prefix="")
    client = TestClient(make_test_app.as_role(app, "admin"))
    resp = client.patch(
        f"/admin/agents/{DEFAULT_ASSISTANT_NAME}/toggle", json={"enabled": False}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


# ===== ChatService actor =====
@pytest.mark.asyncio
async def test_actor_prefers_logged_in_user():
    ctx = SceneContext(
        scene_id=DEFAULT_SCENE_ID,
        code=DEFAULT_SCENE_CODE,
        is_default=True,
        router_mode="direct",
        agent_names=("default_assistant",),
        direct_agent="default_assistant",
    )
    actor = await ChatService._resolve_actor(ctx, "user-1", "visitor-key")
    assert actor.type is ActorType.USER
    assert actor.id == "user-1"


@pytest.mark.asyncio
async def test_actor_visitor_is_created_in_scene(monkeypatch):
    async def get_or_create(scene_id, external_key=None, integration_id=None):
        assert str(scene_id) == DEFAULT_SCENE_ID
        assert external_key == "browser-key"
        # 不带 X-API-Key 的嵌入访客落在匿名命名空间
        assert integration_id is None
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(VisitorRepo, "get_or_create", get_or_create)
    ctx = SceneContext(
        scene_id=DEFAULT_SCENE_ID,
        code=DEFAULT_SCENE_CODE,
        is_default=True,
        router_mode="direct",
        agent_names=("default_assistant",),
        direct_agent="default_assistant",
    )
    actor = await ChatService._resolve_actor(ctx, None, "browser-key")
    assert actor.type is ActorType.VISITOR
    assert actor.key.startswith("visitor:")


@pytest.mark.asyncio
async def test_actor_visitor_uses_integration_namespace(monkeypatch):
    captured: dict = {}

    async def get_or_create(scene_id, external_key=None, integration_id=None):
        captured.update(
            scene_id=str(scene_id), external_key=external_key, integration_id=integration_id
        )
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(VisitorRepo, "get_or_create", get_or_create)
    ctx = SceneContext(
        scene_id=DEFAULT_SCENE_ID,
        code=DEFAULT_SCENE_CODE,
        is_default=True,
        router_mode="direct",
        agent_names=("default_assistant",),
        direct_agent="default_assistant",
    )
    actor = await ChatService._resolve_actor(ctx, None, "cust-42", "i-1")
    # 带接入方时，同一客户标识落在该接入方的命名空间
    assert captured == {
        "scene_id": DEFAULT_SCENE_ID,
        "external_key": "cust-42",
        "integration_id": "i-1",
    }
    assert actor.type is ActorType.VISITOR


# ===== SceneRepo.set_agents 的事务归属 =====
class _FakeSession:
    def __init__(self):
        self.added: list = []
        self.committed = 0

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(scalars=lambda: [])

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        pass

    async def commit(self):
        self.committed += 1


def _patch_session(monkeypatch, fake) -> None:
    from contextlib import asynccontextmanager

    from app.models import repositories as repo_mod

    @asynccontextmanager
    async def _fake_session(db):
        yield fake

    monkeypatch.setattr(repo_mod, "_session", _fake_session)


@pytest.mark.asyncio
async def test_set_agents_commits_when_owning_session(monkeypatch):
    """自开 session 必须提交：曾漏 commit，绑定接口返回 200 但库里没有数据。"""
    fake = _FakeSession()
    _patch_session(monkeypatch, fake)
    await SceneRepo.set_agents(DEFAULT_SCENE_ID, [str(uuid.uuid4())])
    assert fake.committed == 1
    assert len(fake.added) == 1


@pytest.mark.asyncio
async def test_set_agents_leaves_commit_to_caller(monkeypatch):
    """外部传入 db 时事务由调用方控制，仓储不得自行提交。"""
    fake = _FakeSession()
    _patch_session(monkeypatch, fake)
    await SceneRepo.set_agents(DEFAULT_SCENE_ID, [str(uuid.uuid4())], db=SimpleNamespace())
    assert fake.committed == 0
