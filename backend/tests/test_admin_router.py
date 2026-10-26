"""路由分发器只读接口：档案字段、生效提示词实时渲染、无线上修改入口、RBAC（Repo 全 mock）"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import admin_router as ar
from app.core.config import settings
from app.models.repositories import AgentRepo, CannedScriptRepo
from app.schemas.scene import DEFAULT_ASSISTANT_NAME, DEFAULT_SCENE_ID, SceneContext

PATH = ar.router.prefix  # /admin/router


def _dyn_row(name="refund_service", **kw):
    return SimpleNamespace(
        name=name,
        source="dynamic",
        enabled=True,
        description=kw.get("description", "退款咨询：受理退款申请、查询退款进度"),
        routing_keywords=kw.get("routing_keywords", ["退款", "到账"]),
        routing_examples=kw.get("routing_examples", ["钱什么时候退回来"]),
        llm_model=kw.get("llm_model", ""),
        rag_namespace=kw.get("rag_namespace", None),
        mcp_tool_names=kw.get("mcp_tool_names", []),
    )


def _stub_scene_scripts(monkeypatch, rows=None):
    """身份话术警告要读 canned_scripts：按场景归组，测试里不落库。"""

    async def fake_list_all():
        return rows or []

    monkeypatch.setattr(CannedScriptRepo, "list_all", fake_list_all)


def _stub_agents(monkeypatch, *, dynamic_rows=None, code_rows=None):
    """让 list_effective_agents 走 mock：代码 Agent 用真实注册表，动态 Agent 用给定行。"""

    async def fake_list_all():
        return code_rows or []

    async def fake_list_dynamic(enabled_only: bool = True):
        return dynamic_rows or []

    monkeypatch.setattr(AgentRepo, "list_all", fake_list_all)
    monkeypatch.setattr(AgentRepo, "list_dynamic", fake_list_dynamic)


@pytest.fixture
def admin_client(make_test_app):
    app = make_test_app([ar.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


def test_router_info_reports_effective_params(admin_client, monkeypatch):
    _stub_agents(monkeypatch)
    data = admin_client.get(PATH).json()["data"]
    assert data["name"] == "路由分发器"
    assert data["agent_type"] == "router"
    assert data["source"] == "code"
    # 只读契约：明确告诉前端/调用方没有修改入口
    assert data["editable"] is False
    assert data["model"] == settings.router_model
    assert data["max_intents"] == settings.router_max_intents
    assert data["confidence_threshold"] == settings.router_confidence_threshold
    assert data["env_keys"]["model"] == "ROUTER_MODEL"
    # 模板原文与占位符说明都要能查到
    assert "{agent_list}" in data["system_prompt_template"]
    placeholders = {p["name"] for p in data["placeholders"]}
    assert placeholders == {
        "{scene_rules}",
        "{agent_list}",
        "{conversation}",
        "{last_agent}",
        "{max_intents}",
        "{fallback_agent}",
    }


def test_router_prompt_reflects_current_agent_library(admin_client, monkeypatch):
    _stub_agents(monkeypatch, dynamic_rows=[_dyn_row()])
    data = admin_client.get(f"{PATH}/prompt").json()["data"]
    prompt = data["prompt"]
    # 内核代码 Agent 与库中动态 Agent 都进入清单
    assert f"- {DEFAULT_ASSISTANT_NAME}：" in prompt
    assert "- refund_service：" in prompt
    assert "关键词：退款、到账" in prompt
    assert "典型问法：钱什么时候退回来" in prompt
    names = {c["name"] for c in data["candidates"]}
    assert {DEFAULT_ASSISTANT_NAME, "refund_service"} <= names
    assert data["candidate_count"] == len(data["candidates"])
    assert data["fallback_agent"] == DEFAULT_ASSISTANT_NAME
    assert data["fallback_from_candidates"] is True


def test_router_prompt_reports_clue_level_and_truncation(admin_client, monkeypatch):
    many = [f"k{i}" for i in range(15)]
    _stub_agents(
        monkeypatch,
        dynamic_rows=[
            _dyn_row(name="full_service"),
            _dyn_row(name="no_clue_service", routing_keywords=[], routing_examples=[]),
            _dyn_row(name="kw_only_service", routing_keywords=many, routing_examples=[]),
        ],
    )
    data = admin_client.get(f"{PATH}/prompt").json()["data"]
    by_name = {c["name"]: c for c in data["candidates"]}
    assert by_name["full_service"]["clue_level"] == "full"
    assert by_name["no_clue_service"]["clue_level"] == "description_only"
    # 未配线索的动态 Agent 降级为一行描述：不出现「关键词：」行
    assert "- no_clue_service：" in data["prompt"]
    assert "no_clue_service\n  关键词" not in data["prompt"]
    # 超上限的关键词只注入前 12 个，但总量如实回报，便于后台提示"配了却没进提示词"
    kw = by_name["kw_only_service"]
    assert kw["clue_level"] == "keywords_only"
    assert kw["keywords_total"] == 15
    assert len(kw["keywords_injected"]) == 12
    assert kw["keywords_injected"][11] == "k11"
    assert "k12" not in data["prompt"]
    assert kw["keywords_injected"][:2] == ["k0", "k1"]


def test_fallback_degrades_when_preferred_agent_disabled(admin_client, monkeypatch):
    disabled = SimpleNamespace(name=DEFAULT_ASSISTANT_NAME, source="code", enabled=False)
    # 优先候选被禁用，但清单里仍有其它可用 Agent（动态 Agent）→ 降级到首项
    _stub_agents(monkeypatch, code_rows=[disabled], dynamic_rows=[_dyn_row()])
    data = admin_client.get(f"{PATH}/prompt").json()["data"]
    names = {c["name"] for c in data["candidates"]}
    assert DEFAULT_ASSISTANT_NAME not in names
    # 兜底不再指向被禁用的 Agent，而是降级到生效清单首项
    assert data["fallback_agent"] in names
    assert data["fallback_from_candidates"] is False
    assert f"agent_name 填 {data['fallback_agent']}" in data["prompt"]


def test_router_has_no_write_endpoint(admin_client):
    """路由分发器由代码定义：任何写请求都不该被接受（防止误开修改入口）。"""
    for method in ("post", "put", "patch", "delete"):
        # delete 不接受 json 体，按方法给参数
        kwargs = {} if method == "delete" else {"json": {}}
        resp = getattr(admin_client, method)(PATH, **kwargs)
        assert resp.status_code == 405, f"{method.upper()} {PATH} -> {resp.status_code}"
    for method in ("post", "put"):
        resp = getattr(admin_client, method)(f"{PATH}/prompt", json={})
        assert resp.status_code == 405, f"{method.upper()} {PATH}/prompt -> {resp.status_code}"


def test_non_admin_cannot_view_router(make_test_app):
    app = make_test_app([ar.router], prefix="")
    for role in ("agent", "customer"):
        client = TestClient(make_test_app.as_role(app, role))
        resp = client.get(PATH)
        assert resp.status_code == 403, role
        assert resp.json()["detail"]["code"] == 2003


# ===== M3 场景路由档案 =====
def _cand(name: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "display_name": name,
        "description": f"{name} 的适用范围",
        "source": "dynamic",
        "scene_id": "scene-1",
        "routing_keywords": [],
        "routing_examples": [],
        "sort_order": 0,
    }


def _ctx(*, code: str = "mall_order", name: str = "商城订单", candidates=None, **kw) -> SceneContext:
    candidates = tuple(candidates or [])
    base = {
        "scene_id": DEFAULT_SCENE_ID,
        "code": code,
        "is_default": False,
        "router_mode": "auto",
        "agent_names": tuple(c["name"] for c in candidates),
        "direct_agent": None,
        "name": name,
        "status": "active",
        "candidates": candidates,
    }
    base.update(kw)
    return SceneContext(**base)


def test_router_template_matches_legacy_path(admin_client):
    assert admin_client.get(f"{PATH}/template").json()["data"] == admin_client.get(PATH).json()["data"]


def test_router_scenes_lists_summaries(admin_client, monkeypatch):
    ctx = _ctx(candidates=[_cand("order_service"), _cand("refund_service")])

    async def fake_list_all():
        return [SimpleNamespace(id=DEFAULT_SCENE_ID, code="mall_order", display_name="商城订单")]

    async def fake_load(code, require_active=True):
        assert code == "mall_order"
        assert require_active is False  # 后台要能看停用场景的档案
        return ctx

    monkeypatch.setattr(ar.SceneRepo, "list_all", fake_list_all)
    monkeypatch.setattr(ar, "load_scene_context", fake_load)
    _stub_scene_scripts(monkeypatch)

    data = admin_client.get(f"{PATH}/scenes").json()["data"]
    assert len(data) == 1
    assert data[0]["scene_code"] == "mall_order"
    assert data[0]["mode"] == "llm"
    assert data[0]["candidate_count"] == 2
    # 列表不带候选明细与提示词正文
    assert "candidates" not in data[0]
    assert "prompt" not in data[0]


def test_router_scene_detail_returns_candidates(admin_client, monkeypatch):
    ctx = _ctx(candidates=[_cand("order_service"), _cand("refund_service")])

    async def fake_load(code, require_active=True):
        return ctx

    monkeypatch.setattr(ar, "load_scene_context", fake_load)
    _stub_scene_scripts(monkeypatch)
    data = admin_client.get(f"{PATH}/scenes/mall_order").json()["data"]
    assert data["scene_name"] == "商城订单"
    assert data["candidate_count"] == 2
    assert {c["name"] for c in data["candidates"]} == {"order_service", "refund_service"}
    assert isinstance(data["warnings"], list)
    assert len(data["prompt_hash"]) == 12


# ===== 身份话术警告：没配本场景身份话术就提醒 =====
def _scene_detail(client):
    return client.get(f"{PATH}/scenes/mall_order").json()["data"]


def test_scene_detail_warns_without_identity_script(admin_client, monkeypatch):
    ctx = _ctx(candidates=[_cand("order_service")])

    async def fake_load(code, require_active=True):
        return ctx

    monkeypatch.setattr(ar, "load_scene_context", fake_load)
    _stub_scene_scripts(monkeypatch)

    data = _scene_detail(admin_client)
    assert "no_identity_script" in data["warnings"]


def test_scene_detail_clears_warning_with_scene_row(admin_client, monkeypatch):
    ctx = _ctx(candidates=[_cand("order_service")])

    async def fake_load(code, require_active=True):
        return ctx

    monkeypatch.setattr(ar, "load_scene_context", fake_load)
    _stub_scene_scripts(
        monkeypatch,
        [
            SimpleNamespace(
                scene_id=uuid.UUID(DEFAULT_SCENE_ID), key="identity", enabled=True
            )
        ],
    )

    data = _scene_detail(admin_client)
    assert "no_identity_script" not in data["warnings"]


def test_global_identity_row_does_not_clear_warning(admin_client, monkeypatch):
    """只有全局行时仍要提醒：继承全局文案正是要让运营看到的事"""
    ctx = _ctx(candidates=[_cand("order_service")])

    async def fake_load(code, require_active=True):
        return ctx

    monkeypatch.setattr(ar, "load_scene_context", fake_load)
    _stub_scene_scripts(
        monkeypatch, [SimpleNamespace(scene_id=None, key="identity", enabled=True)]
    )

    data = _scene_detail(admin_client)
    assert "no_identity_script" in data["warnings"]


def test_disabled_scene_identity_row_still_warns(admin_client, monkeypatch):
    ctx = _ctx(candidates=[_cand("order_service")])

    async def fake_load(code, require_active=True):
        return ctx

    monkeypatch.setattr(ar, "load_scene_context", fake_load)
    _stub_scene_scripts(
        monkeypatch,
        [
            SimpleNamespace(
                scene_id=uuid.UUID(DEFAULT_SCENE_ID), key="identity", enabled=False
            )
        ],
    )

    data = _scene_detail(admin_client)
    assert "no_identity_script" in data["warnings"]


def test_scene_list_warns_per_scene(admin_client, monkeypatch):
    ctx = _ctx(candidates=[_cand("order_service")])

    async def fake_list_all():
        return [SimpleNamespace(id=DEFAULT_SCENE_ID, code="mall_order", display_name="商城订单")]

    async def fake_load(code, require_active=True):
        return ctx

    monkeypatch.setattr(ar.SceneRepo, "list_all", fake_list_all)
    monkeypatch.setattr(ar, "load_scene_context", fake_load)
    _stub_scene_scripts(monkeypatch)

    summaries = admin_client.get(f"{PATH}/scenes").json()["data"]
    assert "no_identity_script" in summaries[0]["warnings"]


def test_router_scene_prompt_is_scene_scoped(admin_client, monkeypatch):
    ctx = _ctx(candidates=[_cand("order_service"), _cand("refund_service")])

    async def fake_load(code, require_active=True):
        return ctx

    monkeypatch.setattr(ar, "load_scene_context", fake_load)
    data = admin_client.get(f"{PATH}/scenes/mall_order/prompt").json()["data"]
    assert "code: mall_order" in data["prompt"]
    assert "本场景可用 Agent：order_service、refund_service" in data["prompt"]
    assert data["prompt_chars"] > 0
    assert data["prompt_hash"] == data["prompt_hash"][:12]
    assert len(data["prompt_hash"]) == 12


def test_router_scene_test_direct_does_not_call_model(admin_client, monkeypatch):
    ctx = _ctx(
        code="default",
        name="默认场景",
        candidates=[_cand("default_assistant")],
        router_mode="direct",
        is_default=True,
    )

    async def fake_load(code, require_active=True):
        return ctx

    class _NoLLM:
        def __init__(self):  # pragma: no cover - 被创建即失败
            raise AssertionError("直连场景不应创建路由 Agent")

    monkeypatch.setattr(ar, "load_scene_context", fake_load)
    monkeypatch.setattr(ar, "RouterAgent", _NoLLM)

    data = admin_client.post(f"{PATH}/scenes/default/test", json={"message": "你好"}).json()["data"]
    assert data["router_skipped"] is True
    assert data["mode"] == "direct"
    assert data["agent_name"] == "default_assistant"
    assert data["intents"][0]["reason"] == "scene_direct"


def test_router_scene_test_rejects_blank_message(admin_client, monkeypatch):
    async def fake_load(code, require_active=True):
        return _ctx(candidates=[_cand("a")])

    monkeypatch.setattr(ar, "load_scene_context", fake_load)
    resp = admin_client.post(f"{PATH}/scenes/mall_order/test", json={"message": "   "})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


def test_router_scene_test_unconfigured_returns_3005(admin_client, monkeypatch):
    async def fake_load(code, require_active=True):
        return _ctx(candidates=[])

    monkeypatch.setattr(ar, "load_scene_context", fake_load)
    resp = admin_client.post(f"{PATH}/scenes/mall_order/test", json={"message": "你好"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == 3005


def test_router_scene_stats_merges_scene_name(admin_client, monkeypatch):
    async def fake_get(code):
        return SimpleNamespace(code=code, display_name="商城订单")

    async def fake_stats(code, hours=24):
        assert code == "mall_order"
        return {
            "scene_code": code,
            "hours": hours,
            "requests": 10,
            "router_skipped": 2,
            "router_fallback": 1,
            "handover": 1,
            "skip_rate": 0.2,
            "fallback_rate": 0.1,
            "handover_rate": 0.1,
            "avg_router_ms": 123.4,
            "avg_total_latency_ms": 456.7,
        }

    monkeypatch.setattr(ar.SceneRepo, "get_by_code", fake_get)
    monkeypatch.setattr(ar.TraceRepo, "router_stats", fake_stats)

    data = admin_client.get(f"{PATH}/scenes/mall_order/stats").json()["data"]
    assert data["scene_name"] == "商城订单"
    assert data["requests"] == 10
    assert data["fallback_rate"] == 0.1
