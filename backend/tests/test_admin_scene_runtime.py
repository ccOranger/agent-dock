"""后台场景接口的运行参数与用量：生效值回显、默认场景白名单、配额闸门。

运行参数是"缺失=继承全局"的整对象替换语义，后台必须同时看到
"生效值"和"每个键来自哪里"，否则运营改完不知道到底生效了没有。
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_user
from app.api.v1 import admin_scenes as scene_api
from app.models.repositories import AuditRepo, ChatEventRepo, IntegrationRepo, SceneRepo
from app.services import quota_service as qs
from app.services import runtime_config as rc

SELF_ID = str(uuid.uuid4())
SCENE_ID = str(uuid.uuid4())
PATH = scene_api.router.prefix  # /admin/scenes


def _scene(
    code="mall_order",
    display_name="商城订单",
    is_default=False,
    runtime_config=None,
    router_mode="llm",
):
    return SimpleNamespace(
        id=uuid.UUID(SCENE_ID),
        code=code,
        display_name=display_name,
        description=None,
        status="active",
        is_default=is_default,
        router_mode=router_mode,
        router_model=None,
        router_confidence_threshold=None,
        router_max_intents=None,
        fallback_agent_id=None,
        fallback_text=None,
        extra_router_prompt=None,
        runtime_config=runtime_config,
        publish_key=None,
        allowed_origins=[],
        widget_config={},
        created_at=None,
        updated_at=None,
    )


def _client(make_test_app, role="admin", quota=None):
    app = make_test_app([scene_api.router], prefix="")
    user = SimpleNamespace(
        id=SELF_ID,
        username="admin",
        role=role,
        display_name="管理员",
        is_active=True,
        quota=quota or {},
    )
    app.dependency_overrides[current_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def admin_client(make_test_app):
    return _client(make_test_app)


def _mute_audit(monkeypatch):
    async def fake_write(*args, **kw):
        return None

    monkeypatch.setattr(AuditRepo, "write", fake_write)


def _stub_scene(monkeypatch, scene):
    async def fake_get(scene_id):
        return scene

    async def fake_bound(scene_id):
        return []

    async def fake_bound_ids(scene_id):
        return []

    monkeypatch.setattr(SceneRepo, "get", fake_get)
    monkeypatch.setattr(SceneRepo, "list_bound_agents", fake_bound)
    monkeypatch.setattr(SceneRepo, "get_bound_agent_ids", fake_bound_ids)


def _stub_kbs(monkeypatch, rows):
    async def fake_list_all():
        return rows

    monkeypatch.setattr(rc.KnowledgeBaseRepo, "list_all", fake_list_all)


# ===== 列表：今日用量 + 日额度 =====
def test_list_includes_today_usage_and_quota_source(admin_client, monkeypatch):
    rows = [
        {"code": "mall_order", "runtime_config": {"quota": {"daily": 500}}},
        {"code": "it_helpdesk", "runtime_config": {}},
    ]

    async def fake_list():
        return rows

    async def fake_today():
        return {"mall_order": 42}

    monkeypatch.setattr(SceneRepo, "list_with_agent_counts", fake_list)
    monkeypatch.setattr(ChatEventRepo, "count_by_scene_today", fake_today)

    data = admin_client.get(PATH).json()["data"]
    assert data[0]["today_requests"] == 42
    assert data[0]["quota_daily"] == 500
    assert data[0]["quota_source"] == "scene"
    # 没有请求的场景显示 0；未配额时按“不限”并标注默认来源
    assert data[1]["today_requests"] == 0
    assert data[1]["quota_daily"] == 0
    assert data[1]["quota_source"] == "default"


# ===== 详情：生效值与来源 =====
def test_detail_returns_effective_runtime_and_sources(admin_client, monkeypatch):
    _stub_scene(monkeypatch, _scene(runtime_config={"rag": {"top_k": 3}, "history_limit": 20}))

    data = admin_client.get(f"{PATH}/{SCENE_ID}").json()["data"]
    assert data["runtime_config"] == {"rag": {"top_k": 3}, "history_limit": 20}
    assert data["runtime_effective"]["rag"]["top_k"] == 3
    assert data["runtime_sources"]["rag.top_k"] == "scene"
    assert data["runtime_sources"]["rag.min_score"] == "global"
    assert data["runtime_sources"]["history_limit"] == "scene"


# ===== 修改：校验 =====
def test_update_rejects_unknown_runtime_key(admin_client, monkeypatch):
    _stub_scene(monkeypatch, _scene())
    _mute_audit(monkeypatch)

    resp = admin_client.patch(
        f"{PATH}/{SCENE_ID}", json={"runtime_config": {"rag": {"model": "gpt-4o"}}}
    )
    assert resp.status_code == 400
    assert "未知键" in resp.json()["detail"]["message"]


def test_update_rejects_inactive_namespace(admin_client, monkeypatch):
    _stub_scene(monkeypatch, _scene())
    _mute_audit(monkeypatch)
    _stub_kbs(monkeypatch, [SimpleNamespace(namespace="cs_order", is_active=False)])

    resp = admin_client.patch(
        f"{PATH}/{SCENE_ID}", json={"runtime_config": {"rag": {"namespaces": ["cs_order"]}}}
    )
    assert resp.status_code == 400
    assert "cs_order" in resp.json()["detail"]["message"]


def test_update_normalizes_runtime_before_save(admin_client, monkeypatch):
    _stub_scene(monkeypatch, _scene())
    _mute_audit(monkeypatch)
    _stub_kbs(monkeypatch, [SimpleNamespace(namespace="cs_order", is_active=True)])
    captured: dict = {}

    async def fake_update(scene_id, **fields):
        captured.update(fields)
        return _scene(runtime_config=fields.get("runtime_config"))

    monkeypatch.setattr(SceneRepo, "update", fake_update)

    resp = admin_client.patch(
        f"{PATH}/{SCENE_ID}",
        json={"runtime_config": {"rag": {"top_k": 4, "namespaces": [" cs_order "]}}},
    )
    assert resp.status_code == 200, resp.text
    assert captured["runtime_config"] == {"rag": {"top_k": 4, "namespaces": ["cs_order"]}}


def test_update_null_clears_overrides(admin_client, monkeypatch):
    """整对象替换：null 等于恢复“全部继承”，列是 NOT NULL 所以要落成 {}"""
    _stub_scene(monkeypatch, _scene(runtime_config={"history_limit": 10}))
    _mute_audit(monkeypatch)
    captured: dict = {}

    async def fake_update(scene_id, **fields):
        captured.update(fields)
        return _scene(runtime_config=fields.get("runtime_config"))

    monkeypatch.setattr(SceneRepo, "update", fake_update)

    resp = admin_client.patch(f"{PATH}/{SCENE_ID}", json={"runtime_config": None})
    assert resp.status_code == 200, resp.text
    assert captured["runtime_config"] == {}


# ===== 默认场景：只放开展示名/描述/运行参数 =====
def test_default_scene_allows_runtime_config(admin_client, monkeypatch):
    _stub_scene(monkeypatch, _scene(code="default", is_default=True))
    _mute_audit(monkeypatch)
    captured: dict = {}

    async def fake_update(scene_id, **fields):
        captured.update(fields)
        return _scene(code="default", is_default=True, runtime_config=fields.get("runtime_config"))

    monkeypatch.setattr(SceneRepo, "update", fake_update)

    resp = admin_client.patch(
        f"{PATH}/{SCENE_ID}", json={"runtime_config": {"quota": {"daily": 100}}}
    )
    assert resp.status_code == 200, resp.text
    assert captured["runtime_config"] == {"quota": {"daily": 100}}


def test_default_scene_blocks_router_and_agent_changes(admin_client, monkeypatch):
    """默认场景是"无 code 请求"的落点：改路由模式或绑定会把兜底流量打到业务 Agent 上"""
    _stub_scene(monkeypatch, _scene(code="default", is_default=True))
    _mute_audit(monkeypatch)

    for payload in ({"router_mode": "direct"}, {"agent_ids": [str(uuid.uuid4())]}):
        resp = admin_client.patch(f"{PATH}/{SCENE_ID}", json=payload)
        assert resp.status_code == 400
        assert "默认场景" in resp.json()["detail"]["message"]


# ===== 创建：运行参数 + 配额 =====
def test_create_persists_runtime_and_owner(admin_client, monkeypatch):
    held: dict = {}

    async def fake_get_by_code(code):
        return None

    async def fake_create(code, display_name, description=None, **fields):
        held.update({"code": code, **fields})
        return _scene(code=code, display_name=display_name, runtime_config=fields.get("runtime_config"))

    async def fake_grant(scene_id):
        return None

    async def fake_templates(scene_id):
        return []

    monkeypatch.setattr(SceneRepo, "get_by_code", fake_get_by_code)
    monkeypatch.setattr(SceneRepo, "create", fake_create)
    monkeypatch.setattr(IntegrationRepo, "grant_scene_to_all", fake_grant)
    monkeypatch.setattr(scene_api, "ensure_scene_templates_committed", fake_templates)
    _stub_kbs(monkeypatch, [SimpleNamespace(namespace="cs_order", is_active=True)])
    _mute_audit(monkeypatch)

    resp = admin_client.post(
        PATH,
        json={
            "code": "vip_service",
            "display_name": "会员服务",
            "runtime_config": {"rag": {"namespaces": ["cs_order"]}, "quota": {"per_minute": 20}},
        },
    )
    assert resp.status_code == 200, resp.text
    assert held["runtime_config"] == {"rag": {"namespaces": ["cs_order"]}, "quota": {"per_minute": 20}}
    # 配额按 created_by 统计：创建人必须落库，否则这条资源不占任何人的额度
    assert held["created_by"] == SELF_ID


def test_create_blocked_by_scene_quota(make_test_app, monkeypatch):
    async def fake_get_by_code(code):
        return None

    async def fake_count(user_id, kind):
        return 1

    monkeypatch.setattr(SceneRepo, "get_by_code", fake_get_by_code)
    monkeypatch.setattr(qs, "count_owned", fake_count)
    _mute_audit(monkeypatch)

    client = _client(make_test_app, quota={"max_scenes": 1})
    resp = client.post(PATH, json={"code": "vip_service", "display_name": "会员服务"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 3006


def test_agent_role_cannot_create_scene(make_test_app):
    client = _client(make_test_app, role="agent")
    assert client.get(PATH).status_code == 403
    assert client.post(PATH, json={"code": "x", "display_name": "x"}).status_code == 403
