"""动态 Agent + MCP 后台管理：运行时解析与 API（Repo/服务层 mock，不依赖真实 DB/外部服务）"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents import registry
from app.agents.business.dynamic_agent import DynamicBusinessAgent
from app.agents.business.knowledge_agent import KnowledgeAgent
from app.api.v1 import admin as adm
from app.models.repositories import AgentRepo


def _dyn_meta(name="dyn1", **kw):
    return SimpleNamespace(
        name=name,
        source="dynamic",
        enabled=True,
        description=kw.get("description", "动态描述"),
        system_prompt=kw.get("system_prompt", "你是动态助手"),
        llm_model=kw.get("llm_model", ""),
        rag_namespace=kw.get("rag_namespace", None),
        rag_namespaces=kw.get("rag_namespaces", []),
        search_all_knowledge_bases=kw.get("search_all_knowledge_bases", False),
        mcp_tool_names=kw.get("mcp_tool_names", ["mcp_refund"]),
        display_name=kw.get("display_name", name),
        sort_order=kw.get("sort_order", 0),
    )


# ===== 运行时：映射与解析 =====
def test_dynamic_agent_maps_meta_fields():
    meta = _dyn_meta(name="refund_agent", rag_namespace="cs_after_sales", rag_namespaces=["cs_order", "cs_after_sales"])
    agent = DynamicBusinessAgent(meta=meta, user_id="u1", session_id="s1")
    assert agent.name == "refund_agent"
    assert agent.system_prompt == "你是动态助手"
    assert agent.rag_namespace == "cs_after_sales"
    assert agent.rag_namespaces == ["cs_order", "cs_after_sales"]
    assert agent.mcp_tools == ["mcp_refund"]
    assert agent.user_id == "u1"


@pytest.mark.asyncio
async def test_resolve_business_agent_code_and_dynamic_and_unknown(monkeypatch):
    # 代码 Agent：返回代码类实例
    code_agent = await registry.resolve_business_agent("knowledge_qa", user_id="u1")
    assert isinstance(code_agent, KnowledgeAgent)

    # 动态 Agent：get_by_name 返回 dynamic 行
    async def fake_get(name):
        return _dyn_meta(name="dyn1") if name == "dyn1" else None

    monkeypatch.setattr(AgentRepo, "get_by_name", fake_get)
    dyn_agent = await registry.resolve_business_agent("dyn1", user_id="u1")
    assert isinstance(dyn_agent, DynamicBusinessAgent)
    assert dyn_agent.name == "dyn1"

    # 禁用/不存在：返回 None
    assert await registry.resolve_business_agent("nope", user_id="u1") is None


@pytest.mark.asyncio
async def test_list_effective_agents_merges_code_and_dynamic(monkeypatch):
    async def fake_list_dynamic(enabled_only=True):
        return [{"name": "dyn1", "description": "d", "model": "", "rag_namespace": None, "mcp_tools": []}]

    monkeypatch.setattr(registry, "list_dynamic_agents", fake_list_dynamic)
    names = [a["name"] for a in await registry.list_effective_agents()]
    assert "knowledge_qa" in names  # 代码 Agent 保留
    assert "dyn1" in names  # 动态 Agent 合并


# ===== API：动态 Agent CRUD =====
@pytest.fixture
def admin_client(make_test_app):
    app = make_test_app([adm.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


def test_create_agent_rejects_code_name_conflict(admin_client):
    resp = admin_client.post(
        f"{adm.router.prefix}/agents",
        json={
            "name": "knowledge_qa",
            "description": "冲突",
            "system_prompt": "x",
            "mcp_tool_names": [],
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == 3002


def test_create_dynamic_agent_success(admin_client, monkeypatch):
    async def fake_get_by_name(name):
        return None

    async def fake_create_dynamic(**fields):
        captured["fields"] = fields
        return SimpleNamespace(name=fields["name"], id="x")

    async def fake_audit(**kw):
        return None

    captured: dict = {}
    monkeypatch.setattr(AgentRepo, "get_by_name", fake_get_by_name)
    monkeypatch.setattr(AgentRepo, "create_dynamic", fake_create_dynamic)
    monkeypatch.setattr(adm.AuditRepo, "write", fake_audit)

    resp = admin_client.post(
        f"{adm.router.prefix}/agents",
        json={
            "name": "my_dyn",
            "display_name": "我的动态",
            "description": "退款场景",
            "system_prompt": "你是退款助手",
            "mcp_tool_names": [],
        },
    )
    assert resp.status_code == 200
    assert captured["fields"]["name"] == "my_dyn"
    assert captured["fields"]["system_prompt"] == "你是退款助手"


def test_delete_code_agent_forbidden(admin_client, monkeypatch):
    async def fake_get_by_name(name):
        return SimpleNamespace(name=name, source="code")

    monkeypatch.setattr(AgentRepo, "get_by_name", fake_get_by_name)
    resp = admin_client.delete(f"{adm.router.prefix}/agents/order_service")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


def test_agents_list_merges_source(admin_client, monkeypatch):
    async def fake_list_all():
        return [_dyn_meta(name="dyn1", source="dynamic")]

    monkeypatch.setattr(AgentRepo, "list_all", fake_list_all)
    resp = admin_client.get(f"{adm.router.prefix}/agents")
    assert resp.status_code == 200
    data = {a["name"]: a for a in resp.json()["data"]}
    assert data["knowledge_qa"]["source"] == "code"
    assert data["dyn1"]["source"] == "dynamic"
    assert data["dyn1"]["mcp_tools"] == ["mcp_refund"]


def test_agent_write_requires_admin(make_test_app):
    app = make_test_app([adm.router], prefix="")
    agent_client = TestClient(make_test_app.as_role(app, "agent"))
    resp = agent_client.post(
        f"{adm.router.prefix}/agents",
        json={"name": "x1", "description": "d", "system_prompt": "p", "mcp_tool_names": []},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 2003
