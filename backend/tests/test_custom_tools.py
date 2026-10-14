"""自定义工具（进程内版本）改为固定数据后的行为测试 + 本地 MCP 服务导入冒烟测试"""
from pathlib import Path

import pytest

from app.tools.custom import mock_data as md
from app.tools.custom.logistics_tool import QueryLogisticsTool
from app.tools.custom.order_tool import ListOrdersTool, ModifyOrderTool, QueryOrderTool
from app.tools.custom.refund_tool import QueryRefundTool, SubmitRefundTool


@pytest.fixture(autouse=True)
def _isolate_state():
    orders = {k: dict(v) for k, v in md._ORDERS.items()}
    refunds = {k: dict(v) for k, v in md._REFUNDS.items()}
    seq = md._refund_seq
    yield
    md._ORDERS.clear()
    md._ORDERS.update(orders)
    md._REFUNDS.clear()
    md._REFUNDS.update(refunds)
    md._refund_seq = seq


# ---------------- 自定义工具 ----------------

async def test_query_order_tool_ok_and_missing():
    assert (await QueryOrderTool().run("SO20240001"))["ok"] is True
    res = await QueryOrderTool().run("NOPE")
    assert res["ok"] is False and res["error"] == "订单不存在"


async def test_list_orders_tool():
    res = await ListOrdersTool().run("alice")
    assert res["ok"] is True and isinstance(res["data"], list) and res["data"]
    assert (await ListOrdersTool().run("ghost"))["ok"] is False


async def test_modify_order_tool():
    res = await ModifyOrderTool().run("SO20240001", {"remark": "加急"})
    assert res["ok"] is True and res["data"]["remark"] == "加急"
    bad = await ModifyOrderTool().run("SO20240001", {"amount": 1})
    assert bad["ok"] is False


async def test_query_logistics_tool_states():
    ok = await QueryLogisticsTool().run("SO20240001")
    assert ok["ok"] is True and ok["data"]["carrier"]
    # 已支付未发货：无物流但订单存在
    none_res = await QueryLogisticsTool().run("SO20240002")
    assert none_res["ok"] is False and "暂无物流" in none_res["error"]
    # 不存在
    assert (await QueryLogisticsTool().run("NOPE"))["error"] == "订单不存在"


async def test_submit_and_query_refund_tools():
    sub = await SubmitRefundTool().run("SO20240004", "质量问题")
    assert sub["ok"] is True and sub["data"]["status"] == "pending"
    q = await QueryRefundTool().run("SO20240004")
    assert q["ok"] is True and q["data"]["reason"] == "质量问题"
    assert (await QueryRefundTool().run("SO20240001"))["ok"] is False


# ---------------- 本地 MCP 服务：脚本存在 + 可导入 + 工具已注册 ----------------

def test_mcp_server_scripts_exist():
    root = Path(__file__).resolve().parents[1] / "mcp_servers"
    assert (root / "logistics_server.py").is_file()
    assert (root / "refund_server.py").is_file()


def test_logistics_server_registers_tools():
    from mcp_servers import logistics_server

    tool_names = _fastmcp_tool_names(logistics_server.mcp)
    assert {"query_logistics", "query_order_logistics_status"} <= tool_names


def test_refund_server_registers_tools():
    from mcp_servers import refund_server

    tool_names = _fastmcp_tool_names(refund_server.mcp)
    assert {"submit_refund", "query_refund"} <= tool_names


def _fastmcp_tool_names(mcp) -> set[str]:
    """从 FastMCP 实例读取已注册工具名（兼容不同内部结构）。"""
    # 优先异步接口 list_tools()
    try:
        import asyncio

        tools = asyncio.run(mcp.list_tools())
        return {t.name for t in tools}
    except Exception:  # noqa: BLE001
        manager = getattr(mcp, "_tool_manager", None)
        tools = getattr(manager, "_tools", None) or {}
        return set(tools.keys())
