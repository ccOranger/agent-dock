"""MCP「查看工具列表」端点测试：GET /admin/mcp-servers/{id}/tools。

不真连远端：mock ToolRepo.get 提供配置，monkeypatch asyncio.wait_for 旁路 MCPClient 建连，
仅验证 404 分支、成功返回结构、连接异常降级、以及 RBAC（客户禁止）。
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import admin
from app.schemas.api import MCPServerCreate

FAKE_TOOLS = [
    {"name": "echo", "description": "Echoes input", "input_schema": {"type": "object"}},
    {
        "name": "get_sum",
        "description": "Add numbers",
        "input_schema": {"type": "object", "properties": {"a": {"type": "number"}}},
    },
]


def _mcp_row():
    return SimpleNamespace(
        id="t-1",
        name="mcp_demo",
        type="mcp",
        enabled=True,
        config={
            "transport": "stdio",
            "command": "",
            "args": ["mcp_servers/demo_server.py"],
            "timeout_seconds": 5,
        },
    )


@pytest.fixture
def client(make_test_app):
    app = make_test_app([admin.router], prefix="")
    return TestClient(make_test_app.as_role(app, "agent"))


def _stub_get(monkeypatch, row):
    async def fake_get(server_id):
        return row

    monkeypatch.setattr(admin.ToolRepo, "get", fake_get)


def _stub_wait_for(monkeypatch, result=None, exc=None):
    async def fake_wait_for(coro, timeout=None):
        # 旁路真实建连：关闭协程避免 "never awaited" 警告，直接返回/抛出
        if hasattr(coro, "close"):
            coro.close()
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(admin.asyncio, "wait_for", fake_wait_for)


def test_tools_not_found_returns_404(client, monkeypatch):
    _stub_get(monkeypatch, None)
    resp = client.get(f"{admin.router.prefix}/mcp-servers/missing/tools")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == 3001


def test_tools_rejects_non_mcp_row(client, monkeypatch):
    _stub_get(monkeypatch, SimpleNamespace(id="x", name="cust", type="custom", config={}))
    resp = client.get(f"{admin.router.prefix}/mcp-servers/x/tools")
    assert resp.status_code == 404


def test_tools_success_shape(client, monkeypatch):
    _stub_get(monkeypatch, _mcp_row())
    _stub_wait_for(monkeypatch, result=FAKE_TOOLS)
    resp = client.get(f"{admin.router.prefix}/mcp-servers/t-1/tools")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["server_name"] == "mcp_demo"
    names = [t["name"] for t in data["tools"]]
    assert names == ["echo", "get_sum"]
    assert data["tools"][0]["input_schema"] == {"type": "object"}


def test_tools_connection_error_degrades_to_ok_false(client, monkeypatch):
    _stub_get(monkeypatch, _mcp_row())
    _stub_wait_for(monkeypatch, exc=RuntimeError("connection refused"))
    resp = client.get(f"{admin.router.prefix}/mcp-servers/t-1/tools")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert "connection refused" in data["error"]


def test_customer_forbidden_on_tools(make_test_app):
    app = make_test_app([admin.router], prefix="")
    customer_client = TestClient(make_test_app.as_role(app, "customer"))
    resp = customer_client.get(f"{admin.router.prefix}/mcp-servers/t-1/tools")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 2003


def test_mcp_description_roundtrip():
    """description 写入 config 且能被 _config_to_body 还原（供 PUT 合并保留）。"""
    body = MCPServerCreate(name="mcp_demo", transport="stdio", args=["s.py"], description="物流查询服务")
    config = admin._build_mcp_config(body)
    assert config["description"] == "物流查询服务"
    assert admin._config_to_body(config)["description"] == "物流查询服务"


def test_mcp_description_empty_not_persisted():
    """空描述不写入 config；还原时缺省为 None（可清空）。"""
    body = MCPServerCreate(name="mcp_demo", transport="stdio", args=["s.py"], description="")
    config = admin._build_mcp_config(body)
    assert "description" not in config
    assert admin._config_to_body(config)["description"] is None
