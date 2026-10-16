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
        custom_tool_names=kw.get("custom_tool_names", []),
        display_name=kw.get("display_name", name),
        sort_order=kw.get("sort_order", 0),
    )


# ===== 运行时：映射与解析 =====
def test_dynamic_agent_maps_meta_fields():
    meta = _dyn_meta(
        name="refund_agent",
        rag_namespace="cs_after_sales",
        rag_namespaces=["cs_order", "cs_after_sales"],
        custom_tool_names=["query_order", "create_ticket"],
    )
    agent = DynamicBusinessAgent(meta=meta, user_id="u1", session_id="s1")
    assert agent.name == "refund_agent"
    assert agent.system_prompt == "你是动态助手"
    assert agent.rag_namespace == "cs_after_sales"
    assert agent.rag_namespaces == ["cs_order", "cs_after_sales"]
    assert agent.mcp_tools == ["mcp_refund"]
    assert agent.custom_tool_names == ["query_order", "create_ticket"]
    assert agent.user_id == "u1"


@pytest.mark.asyncio
async def test_resolve_business_agent_code_and_dynamic_and_unknown(monkeypatch):
    async def fake_get(name):
        if name == "dyn1":
            return _dyn_meta(name="dyn1")
        if name == "order_service":  # 代码 Agent 在库中被禁用
            return SimpleNamespace(name="order_service", source="code", enabled=False)
        return None

    monkeypatch.setattr(AgentRepo, "get_by_name", fake_get)

    # 代码 Agent：DB 无行或启用时返回代码类实例
    code_agent = await registry.resolve_business_agent("knowledge_qa", user_id="u1")
    assert isinstance(code_agent, KnowledgeAgent)

    # 代码 Agent 被禁用：即使代码注册表里有也不再命中
    assert await registry.resolve_business_agent("order_service", user_id="u1") is None

    # 动态 Agent：get_by_name 返回 dynamic 行
    dyn_agent = await registry.resolve_business_agent("dyn1", user_id="u1")
    assert isinstance(dyn_agent, DynamicBusinessAgent)
    assert dyn_agent.name == "dyn1"

    # 不存在：返回 None
    assert await registry.resolve_business_agent("nope", user_id="u1") is None


@pytest.mark.asyncio
async def test_list_effective_agents_merges_code_and_dynamic(monkeypatch):
    async def fake_list_dynamic(enabled_only=True):
        return [{"name": "dyn1", "description": "d", "model": "", "rag_namespace": None, "mcp_tools": []}]

    async def fake_list_all():
        return []

    monkeypatch.setattr(registry, "list_dynamic_agents", fake_list_dynamic)
    monkeypatch.setattr(AgentRepo, "list_all", fake_list_all)
    names = [a["name"] for a in await registry.list_effective_agents()]
    assert "knowledge_qa" in names  # 代码 Agent 保留
    assert "dyn1" in names  # 动态 Agent 合并


@pytest.mark.asyncio
async def test_list_effective_agents_excludes_disabled_code(monkeypatch):
    async def fake_list_dynamic(enabled_only=True):
        return []

    async def fake_list_all():
        return [SimpleNamespace(name="order_service", source="code", enabled=False)]

    monkeypatch.setattr(registry, "list_dynamic_agents", fake_list_dynamic)
    monkeypatch.setattr(AgentRepo, "list_all", fake_list_all)
    names = [a["name"] for a in await registry.list_effective_agents()]
    assert "order_service" not in names  # 被禁用的代码 Agent 从路由候选中剔除
    assert "knowledge_qa" in names


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
    # 代码 Agent 也应带出系统提示词/注册工具/MCP（从代码静态解析，无需 DB）
    order = data["order_service"]
    assert order["system_prompt"]
    assert "query_order" in order["custom_tool_names"]
    assert "mcp_logistics" in order["mcp_tools"]
    assert "memory" in order["registered_tools"]
    # knowledge_qa 仅挂 memory/rag，无自定义工具
    assert data["knowledge_qa"]["registered_tools"] == ["memory", "rag"]
    assert data["knowledge_qa"]["custom_tool_names"] == []
    # 动态 Agent 也应有注册工具（无主库/自定义工具时仅 memory）
    assert data["dyn1"]["registered_tools"] == ["memory"]


def test_agent_detail_code_returns_fields(admin_client, monkeypatch):
    async def fake_get_by_name(name):
        return None

    monkeypatch.setattr(AgentRepo, "get_by_name", fake_get_by_name)
    resp = admin_client.get(f"{adm.router.prefix}/agents/knowledge_qa")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "code"
    assert data["enabled"] is True
    assert data["system_prompt"]
    assert data["registered_tools"] == ["memory", "rag"]


def test_agent_detail_dynamic_registered_tools(admin_client, monkeypatch):
    async def fake_get_by_name(name):
        return _dyn_meta(name="dynx", rag_namespace="cs_a", custom_tool_names=["query_order", "create_ticket"])

    monkeypatch.setattr(AgentRepo, "get_by_name", fake_get_by_name)
    resp = admin_client.get(f"{adm.router.prefix}/agents/dynx")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "dynamic"
    # memory + rag(有主库) + 已登记自定义工具；MCP 不并入
    assert data["registered_tools"] == ["memory", "rag", "query_order", "create_ticket"]


def test_agent_write_requires_admin(make_test_app):
    app = make_test_app([adm.router], prefix="")
    agent_client = TestClient(make_test_app.as_role(app, "agent"))
    resp = agent_client.post(
        f"{adm.router.prefix}/agents",
        json={"name": "x1", "description": "d", "system_prompt": "p", "mcp_tool_names": []},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 2003
