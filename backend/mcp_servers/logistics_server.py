"""mcp_logistics —— 物流查询本地 MCP 服务（FastMCP, stdio）

对外提供两个工具：
- query_logistics(order_id)                按订单号查物流轨迹
- query_order_logistics_status(order_id)   仅返回精简的运输状态文本

数据来源与进程内自定义工具一致：app.packs.customer_service.tools.mock_data。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 以脚本绝对路径被拉起时，把 backend 根目录加入 sys.path，
# 从而能 import app.packs.customer_service.tools.mock_data（该链路各 __init__ 均为空，无副作用）。
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app.packs.customer_service.tools import mock_data  # noqa: E402

mcp = FastMCP("mcp_logistics")


@mcp.tool()
def query_logistics(order_id: str) -> dict:
    """根据订单号查询物流轨迹（快递公司、运单号、状态、逐条轨迹、预计送达）。

    order_id 形如 SO20240001。若订单不存在或暂无物流，返回 ok=false 与原因。
    """
    rec = mock_data.get_logistics(order_id)
    if rec is None:
        order = mock_data.get_order(order_id)
        if order is None:
            return {"ok": False, "error": "订单不存在"}
        return {
            "ok": False,
            "error": f"订单 {order_id} 当前状态为「{order['status_text']}」，暂无物流记录",
        }
    return {"ok": True, "data": rec}


@mcp.tool()
def query_order_logistics_status(order_id: str) -> dict:
    """仅返回订单的运输状态摘要（状态码 + 中文状态 + 快递公司 + 运单号）。"""
    rec = mock_data.get_logistics(order_id)
    if rec is None:
        return {"ok": False, "error": "无物流记录"}
    return {
        "ok": True,
        "data": {
            "order_id": order_id,
            "status": rec["status"],
            "status_text": rec["status_text"],
            "carrier": rec["carrier"],
            "tracking_no": rec["tracking_no"],
            "eta": rec.get("eta"),
        },
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
