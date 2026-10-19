"""路由分发器只读接口：档案字段、生效提示词实时渲染、无线上修改入口、RBAC（Repo 全 mock）"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import admin_router as ar
from app.core.config import settings
from app.models.repositories import AgentRepo

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
    assert placeholders == {"{agent_list}", "{conversation}", "{last_agent}", "{max_intents}", "{fallback_agent}"}


def test_router_prompt_reflects_current_agent_library(admin_client, monkeypatch):
    _stub_agents(monkeypatch, dynamic_rows=[_dyn_row()])
    data = admin_client.get(f"{PATH}/prompt").json()["data"]
    prompt = data["prompt"]
    # 代码 Agent 与库中动态 Agent 都进入清单
    assert "- order_service：" in prompt
    assert "- refund_service：" in prompt
    assert "关键词：退款、到账" in prompt
    assert "典型问法：钱什么时候退回来" in prompt
    names = {c["name"] for c in data["candidates"]}
    assert {"order_service", "refund_service"} <= names
    assert data["candidate_count"] == len(data["candidates"])
    assert data["fallback_agent"] == "knowledge_qa"
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


def test_fallback_degrades_when_knowledge_qa_disabled(admin_client, monkeypatch):
    disabled = SimpleNamespace(name="knowledge_qa", source="code", enabled=False)
    _stub_agents(monkeypatch, code_rows=[disabled])
    data = admin_client.get(f"{PATH}/prompt").json()["data"]
    names = {c["name"] for c in data["candidates"]}
    assert "knowledge_qa" not in names
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
