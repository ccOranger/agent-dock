"""自定义工具：订单查询 / 修改。

历史版本通过 httpx 访问固定假域名 http://order-service:8080（并不存在，必然失败）。
现改为读取固定数据集 app.tools.custom.mock_data，可直接演示与测试。
"""
from app.tools.custom import mock_data
from app.tools.custom.base import BaseTool


class QueryOrderTool(BaseTool):
    name = "query_order"
    description = "根据订单号查询订单状态。仅用于订单相关查询。"
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号，例如 SO20240001"},
        },
        "required": ["order_id"],
        "additionalProperties": False,
    }

    async def run(self, order_id: str):
        order = mock_data.get_order(order_id)
        if order is None:
            return {"ok": False, "error": "订单不存在"}
        return {"ok": True, "data": order}


class ListOrdersTool(BaseTool):
    name = "list_orders"
    description = "根据客户 ID 列出该客户的全部订单（当客户未提供订单号时先用于定位）。"
    parameters = {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "客户 ID，例如 alice"},
        },
        "required": ["customer_id"],
        "additionalProperties": False,
    }

    async def run(self, customer_id: str):
        orders = mock_data.list_orders(customer_id)
        if not orders:
            return {"ok": False, "error": "未查询到该客户的订单"}
        return {"ok": True, "data": orders}


class ModifyOrderTool(BaseTool):
    name = "modify_order"
    description = "修改订单信息（地址/备注等）。修改前必须与客户确认。"
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号"},
            "fields": {"type": "object", "description": "需要修改的字段键值对（仅支持 address/remark）"},
        },
        "required": ["order_id", "fields"],
        "additionalProperties": False,
    }

    async def run(self, order_id: str, fields: dict):
        order, error = mock_data.update_order(order_id, fields or {})
        if error:
            return {"ok": False, "error": error}
        return {"ok": True, "data": order}
