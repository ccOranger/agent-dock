"""mcp_refund —— 退款本地 MCP 服务（FastMCP, stdio）

对外提供两个工具：
- submit_refund(order_id, reason)  提交退款申请，返回退款单号与受理状态
- query_refund(order_id)           按订单号查询退款进度

数据来源与进程内自定义工具一致：app.packs.customer_service.tools.mock_data。
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app.packs.customer_service.tools import mock_data  # noqa: E402

mcp = FastMCP("mcp_refund")


@mcp.tool()
def submit_refund(order_id: str, reason: str) -> dict:
    """提交退款申请。返回退款单号与受理状态；不承诺具体退款金额与到账时间。

    order_id 形如 SO20240001；reason 为退款原因。已取消订单或已存在退款单会拒绝。
    """
    refund, error = mock_data.submit_refund(order_id, reason)
    if error:
        return {"ok": False, "error": error}
    return {"ok": True, "data": refund}


@mcp.tool()
def query_refund(order_id: str) -> dict:
    """根据订单号查询退款进度（退款单号、状态、原因、申请时间）。"""
    refund = mock_data.get_refund_by_order(order_id)
    if refund is None:
        return {"ok": False, "error": "未查询到该订单的退款记录"}
    return {"ok": True, "data": refund}


if __name__ == "__main__":
    mcp.run(transport="stdio")
