"""自定义工具目录/注册器 + 后台「自定义工具」接口（Repo/DB mock，不依赖真实数据库）。"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from hello_agents.tools import ToolRegistry

from app.api.v1 import admin as adm
from app.models.repositories import ToolRepo
from app.tools import custom_catalog
from app.tools.custom_loader import register_custom_tools

_EXPECTED_NAMES = {
    "echo",
    "query_order",
    "list_orders",
    "modify_order",
    "query_logistics",
    "submit_refund",
    "query_refund",
    "create_ticket",
    "update_ticket",
    "escalate",
    "create_it_ticket",
    "update_it_ticket",
    "escalate_it",
    "query_it_asset",
}


# ===== 代码目录 =====
def test_catalog_lists_all_tools_with_specs():
    specs = custom_catalog.list_specs()
    assert {s["name"] for s in specs} == _EXPECTED_NAMES
    for s in specs:
        assert s["name"] and s["description"]
        assert isinstance(s["parameters"], dict) and "properties" in s["parameters"]


def test_catalog_exists_and_build():
    assert custom_catalog.exists("query_order") is True
    assert custom_catalog.exists("nope") is False
    tool = custom_catalog.build("query_order")
    assert tool is not None and tool.name == "query_order"
    assert custom_catalog.build("nope") is None


@pytest.mark.asyncio
async def test_echo_tool_returns_input():
    """echo 零依赖自检工具：原样回显入参，用于验证工具调用链路打通。"""
    tool = custom_catalog.build("echo")
    assert tool is not None and tool.name == "echo"
    res = await tool.run(text="hello 链路")
    assert res == {"ok": True, "data": {"text": "hello 链路"}}


# ===== 注册器 =====
def test_register_custom_tools_known_and_unknown():
    reg = ToolRegistry()
    registered = register_custom_tools(reg, ["query_order", "not_a_tool", "create_ticket"])
    # 仅返回代码目录中存在的名字，未知名被跳过
    assert registered == ["query_order", "create_ticket"]


# ===== 后台接口：自定义工具登记 =====
@pytest.fixture
def admin_client(make_test_app):
    app = make_test_app([adm.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


def test_list_custom_tools_returns_catalog(admin_client, monkeypatch):
    async def fake_list_by_type(type_):
        return []

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    resp = admin_client.get(f"{adm.router.prefix}/custom-tools")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert {d["name"] for d in data} == _EXPECTED_NAMES
    for d in data:
        assert d["registered"] is False and d["enabled"] is False


def test_list_custom_tools_marks_registered(admin_client, monkeypatch):
    row = SimpleNamespace(name="query_order", enabled=True, config={"description": "覆盖描述"})

    async def fake_list_by_type(type_):
        return [row]

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    resp = admin_client.get(f"{adm.router.prefix}/custom-tools")
    item = {d["name"]: d for d in resp.json()["data"]}["query_order"]
    assert item["registered"] is True and item["enabled"] is True
    assert item["display_description"] == "覆盖描述"


def test_register_custom_tool_rejects_unknown_name(admin_client):
    resp = admin_client.post(f"{adm.router.prefix}/custom-tools", json={"name": "nope"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


def test_register_custom_tool_success(admin_client, monkeypatch):
    captured: dict = {}

    async def fake_list_by_type(type_):
        return []

    async def fake_get_by_name(name):  # MCP 同名探测
        return None

    async def fake_create_custom(name, schema_=None, config=None, enabled=True):
        captured.update({"name": name, "schema": schema_, "config": config, "enabled": enabled})
        return SimpleNamespace(name=name, id="x")

    async def fake_audit(**kw):
        return None

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    monkeypatch.setattr(ToolRepo, "get_by_name", fake_get_by_name)
    monkeypatch.setattr(ToolRepo, "create_custom", fake_create_custom)
    monkeypatch.setattr(adm.AuditRepo, "write", fake_audit)

    resp = admin_client.post(
        f"{adm.router.prefix}/custom-tools",
        json={"name": "query_order", "description": "自定义显示名", "enabled": True},
    )
    assert resp.status_code == 200
    assert captured["name"] == "query_order"
    assert captured["config"] == {"description": "自定义显示名"}


# ===== 后台接口：动态 Agent 绑定校验 =====
def test_create_agent_rejects_unregistered_custom_tool(admin_client, monkeypatch):
    async def fake_agent_get(name):
        return None

    async def fake_list_by_type(type_):
        return []  # 无任何已登记 custom 工具

    monkeypatch.setattr(adm.AgentRepo, "get_by_name", fake_agent_get)
    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    resp = admin_client.post(
        f"{adm.router.prefix}/agents",
        json={
            "name": "d1",
            "description": "演示",
            "system_prompt": "你是助手",
            "mcp_tool_names": [],
            "custom_tool_names": ["query_order"],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


def test_create_agent_rejects_unknown_custom_tool(admin_client, monkeypatch):
    async def fake_agent_get(name):
        return None

    monkeypatch.setattr(adm.AgentRepo, "get_by_name", fake_agent_get)
    resp = admin_client.post(
        f"{adm.router.prefix}/agents",
        json={
            "name": "d2",
            "description": "演示",
            "system_prompt": "你是助手",
            "mcp_tool_names": [],
            "custom_tool_names": ["ghost_tool"],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001
