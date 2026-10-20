"""物流查询工具（进程内自定义版本）。

历史版本访问固定假域名 http://logistics-service:8080（不存在）。现改为读取
共享固定数据集 mock_data；同一份数据也被本地 MCP 服务 mcp_logistics 使用。
"""
from app.packs.customer_service.tools import mock_data
from app.tools.custom.base import BaseTool


class QueryLogisticsTool(BaseTool):
    name = "query_logistics"
    description = "根据订单号查询物流轨迹。"
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号，例如 SO20240001"},
        },
        "required": ["order_id"],
        "additionalProperties": False,
    }

    async def run(self, order_id: str):
        rec = mock_data.get_logistics(order_id)
        if rec is None:
            order = mock_data.get_order(order_id)
            if order is None:
                return {"ok": False, "error": "订单不存在"}
            return {"ok": False, "error": f"订单 {order_id} 当前状态为「{order['status_text']}」，暂无物流记录"}
        return {"ok": True, "data": rec}
