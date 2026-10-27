"""M4 场景隔离测试：话术 / 知识库 / 记忆 / 会话 / 链路五类不串场。

隔离的判定标准不是「界面上看不到」，而是「运行时拿不到」：
- 话术：同 key 场景行覆盖全局行，场景行停用不回落到全局文案；
- 知识库：全局资源不记场景归属，search_all 覆盖全部启用库，跨业务靠命名纪律；
- 记忆：镜像行按 user/visitor 二选一归属，晋升时不能把 visitor 写成 NULL；
- 会话：跨场景查看按「不存在」处理，不泄露另一个场景的会话存在性；
- 链路：摘要与筛选都带 scene 维度，后台能按场景归因。

Repo 全部 mock，不依赖真实库（与 test_admin_canned_scripts 一致）。
"""
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents import canned_scripts as cs
from app.agents import orchestrator as orch_mod
from app.api.v1 import admin_canned_scripts as acs
from app.api.v1 import admin_traces as at
from app.api.v1 import sessions as sess_api
from app.core import context_engineer as ce
from app.models.repositories import CannedScriptRepo, KnowledgeBaseRepo, MemoryRepo, TraceRepo


# ===== 1. 固定话术：场景覆盖与停用语义 =====
def _script(key, priority, scene_id=None, text="", enabled=True):
    return SimpleNamespace(
        key=key, priority=priority, scene_id=scene_id, reply_text=text, enabled=enabled
    )


def test_canned_scene_override_beats_global():
    scene_id = uuid.uuid4()
    merged = CannedScriptRepo._merge_scene_overrides(
        [
            _script("identity", 20, None, "全局身份"),
            _script("identity", 5, scene_id, "场景身份"),
            _script("probing", 10, None, "探听拒答"),
        ]
    )
    # 同 key 只留场景行；顺序按运行时优先级
    assert [r.key for r in merged] == ["identity", "probing"]
    assert merged[0].reply_text == "场景身份"


def test_canned_disabled_scene_override_does_not_fall_back_to_global():
    """场景行停用 = 本场景这条话术不生效，而不是悄悄换成全局文案。"""
    scene_id = uuid.uuid4()
    merged = CannedScriptRepo._merge_scene_overrides(
        [
            _script("identity", 20, None, "全局身份"),
            _script("identity", 5, scene_id, "场景身份", enabled=False),
        ]
    )
    assert [r.key for r in merged if r.enabled] == []


@pytest.mark.asyncio
async def test_canned_runtime_passes_scene_id_to_repo(monkeypatch):
    seen: dict = {}

    async def fake_list_enabled(scene_id=None):
        seen["scene_id"] = scene_id
        return []

    monkeypatch.setattr(CannedScriptRepo, "list_enabled", fake_list_enabled)
    assert await cs.match_canned_script("你好", "scene-a") is None
    assert seen["scene_id"] == "scene-a"


def test_canned_create_is_scoped_to_scene(make_test_app, monkeypatch):
    app = make_test_app([acs.router], prefix="")
    client = TestClient(make_test_app.as_role(app, "admin"))
    scene_id = uuid.uuid4()
    captured: dict = {}

    async def fake_scene(code):
        return SimpleNamespace(id=scene_id)

    async def fake_get_by_key(key, scene_id=None):
        captured["lookup_scene"] = scene_id
        return None

    async def fake_create(**fields):
        captured["create"] = fields
        return SimpleNamespace(
            id="id-1",
            scene_id=fields.get("scene_id"),
            key=fields["key"],
            title=fields.get("title"),
            category=fields["category"],
            patterns=fields["patterns"],
            keywords=fields["keywords"],
            reply_text=fields["reply_text"],
            priority=fields["priority"],
            enabled=fields["enabled"],
            is_builtin=False,
            created_at=None,
            updated_at=None,
        )

    async def fake_audit(*a, **kw):
        return None

    monkeypatch.setattr(acs.SceneRepo, "get_by_code", fake_scene)
    monkeypatch.setattr(acs.CannedScriptRepo, "get_by_key", fake_get_by_key)
    monkeypatch.setattr(acs.CannedScriptRepo, "create", fake_create)
    monkeypatch.setattr(acs.AuditRepo, "write", fake_audit)

    resp = client.post(
        "/admin/canned-scripts",
        json={
            "key": "hours",
            "scene_code": "mall_order",
            "patterns": ["营业时间"],
            "reply_text": "每天 9:00-21:00。",
        },
    )
    assert resp.status_code == 200, resp.text
    # 冲突检查按 (场景, key) 定位，不占用全局 key；落库时带 scene_id
    assert captured["lookup_scene"] == str(scene_id)
    assert captured["create"]["scene_id"] == scene_id


# ===== 2. 知识库：全局资源，search_all 覆盖全部启用库 =====
@pytest.mark.asyncio
async def test_rag_search_all_covers_all_active_bases(monkeypatch):
    rows = [
        SimpleNamespace(namespace="kb_a", is_active=True),
        SimpleNamespace(namespace="kb_b", is_active=True),
        SimpleNamespace(namespace="kb_off", is_active=False),
    ]

    async def fake_list_all():
        return rows

    monkeypatch.setattr(KnowledgeBaseRepo, "list_all", fake_list_all)
    agent = SimpleNamespace(
        rag_namespace=None, rag_namespaces=[], search_all_knowledge_bases=True
    )

    # 不再按场景过滤：停用库排除，启用库全部覆盖
    assert await orch_mod.Orchestrator._resolve_rag_namespaces(agent) == ["kb_a", "kb_b"]


@pytest.mark.asyncio
async def test_rag_explicit_namespace_survives_without_search_all():
    agent = SimpleNamespace(
        rag_namespace="cs_default",
        rag_namespaces=["cs_order", "cs_default"],
        search_all_knowledge_bases=False,
    )
    assert await orch_mod.Orchestrator._resolve_rag_namespaces(agent) == [
        "cs_default",
        "cs_order",
    ]


# ===== 3. 记忆：actor 归属列 =====
def test_memory_actor_condition_uses_correct_column():
    visitor_id = uuid.uuid4()
    cond = MemoryRepo._actor_cond(f"visitor:{visitor_id}")[0]
    assert cond.left.key == "visitor_id"
    assert cond.right.value == visitor_id

    user_id = uuid.uuid4()
    cond = MemoryRepo._actor_cond(str(user_id))[0]
    assert cond.left.key == "user_id"
    assert cond.right.value == user_id


@pytest.mark.asyncio
async def test_visitor_memory_mirror_keeps_visitor_column(monkeypatch):
    """访客镜像行只有 visitor_id：晋升写回时不能把归属写成 NULL（会丢隔离）。"""
    visitor_id = uuid.uuid4()
    row = SimpleNamespace(
        user_id=None,
        visitor_id=visitor_id,
        memory_uid="m-1",
        content="我要退款",
        importance=0.8,
        session_id="s-1",
        source_message_id=None,
        metadata_={"agent_name": "refund_service"},
    )
    captured: dict = {}

    class _Tool:
        async def run(self, payload):
            return None

    async def fake_pool(fn, payload):
        await fn(payload)

    async def fake_upsert(**kw):
        captured.update(kw)

    monkeypatch.setattr(ce, "get_memory_tool", lambda subject: _Tool())
    monkeypatch.setattr(ce, "run_in_pool", fake_pool)
    monkeypatch.setattr(MemoryRepo, "upsert_mirror", fake_upsert)

    await ce.ContextEngineer._persist_longterm(f"visitor:{visitor_id}", [row])
    assert captured["user_id"] is None
    assert captured["visitor_id"] == visitor_id


# ===== 4. 会话：跨场景访问按不存在处理 =====
def test_session_detail_is_scene_scoped(make_test_app, monkeypatch):
    app = make_test_app([sess_api.router], prefix="")
    client = TestClient(make_test_app.as_role(app, "customer"))
    scene_a, scene_b = uuid.uuid4(), uuid.uuid4()
    session_id = uuid.uuid4()

    async def fake_load(code, require_active=True):
        return SimpleNamespace(scene_id=str(scene_b if code == "scene_b" else scene_a))

    async def fake_get(sid, db=None):
        return SimpleNamespace(
            id=session_id,
            scene_id=scene_b,
            user_id="u-1",
            title="订单咨询",
            status="active",
            last_route_to=None,
            created_at=datetime(2025, 1, 1, 10, 0, 0),
            updated_at=datetime(2025, 1, 1, 10, 0, 0),
        )

    monkeypatch.setattr(sess_api, "load_scene_context", fake_load)
    monkeypatch.setattr(sess_api.SessionRepo, "get", fake_get)

    # A 场景访问 B 场景的会话：404，不泄露存在性
    resp = client.get(f"/sessions/{session_id}?scene_code=scene_a")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == 3001

    # 本场景访问正常
    resp = client.get(f"/sessions/{session_id}?scene_code=scene_b")
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == str(session_id)


def test_session_list_passes_scene_id(make_test_app, monkeypatch):
    app = make_test_app([sess_api.router], prefix="")
    client = TestClient(make_test_app.as_role(app, "customer"))
    scene_id = uuid.uuid4()
    captured: dict = {}

    async def fake_load(code, require_active=True):
        return SimpleNamespace(scene_id=str(scene_id))

    async def fake_list(user_id, page, page_size, scene_id=None):
        captured["scene_id"] = scene_id
        return [], 0

    monkeypatch.setattr(sess_api, "load_scene_context", fake_load)
    monkeypatch.setattr(sess_api.SessionRepo, "list_by_user", fake_list)

    resp = client.get("/sessions?scene_code=mall_order")
    assert resp.status_code == 200
    assert captured["scene_id"] == str(scene_id)


# ===== 5. 链路：场景字段与筛选 =====
def _trace_row():
    return SimpleNamespace(
        id=uuid.uuid4(),
        trace_id="t-1",
        session_id=None,
        user_id=None,
        scene_code="mall_order",
        router_skipped=False,
        router_fallback=False,
        router_ms=321,
        prompt_hash="abc123",
        user_message_id=None,
        assistant_message_id=None,
        intent_count=1,
        agent_names=["refund_service"],
        agent_sources=["code"],
        llm_models=[],
        mcp_server_names=[],
        tool_names=[],
        rag_namespaces=[],
        memory_hit_count=0,
        rag_chunk_count=0,
        tool_call_count=0,
        tool_error_count=0,
        confidence=0.9,
        outcome="answered",
        handover_reason=None,
        error_code=None,
        error_message=None,
        ttft_ms=10,
        total_latency_ms=20,
        response_chars=30,
        spans=[],
        created_at=None,
    )


def test_trace_summary_exposes_scene_and_router_fields():
    data = TraceRepo._summary(_trace_row())
    assert data["scene_code"] == "mall_order"
    assert data["router_ms"] == 321
    assert data["prompt_hash"] == "abc123"
    assert data["router_skipped"] is False
    assert data["router_fallback"] is False


def test_trace_list_passes_scene_filter(make_test_app, monkeypatch):
    app = make_test_app([at.router], prefix="")
    client = TestClient(make_test_app.as_role(app, "agent"))
    captured: dict = {}

    async def fake_list(**kw):
        captured.update(kw)
        return [], 0

    monkeypatch.setattr(at.TraceRepo, "list_filtered", fake_list)
    resp = client.get("/admin/traces?scene_code=mall_order")
    assert resp.status_code == 200
    assert captured["scene_code"] == "mall_order"
