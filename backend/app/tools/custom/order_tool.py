"""自定义工具：继承项目内 BaseTool（适配 hello_agents.Tool）"""
import httpx

from app.tools.custom.base import BaseTool


class QueryOrderTool(BaseTool):
    name = "query_order"
    description = "根据订单号查询订单状态。仅用于订单相关查询。"
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号"},
        },
        "required": ["order_id"],
        "additionalProperties": False,
    }
    timeout = 10
    base_url = "http://order-service:8080"

    async def run(self, order_id: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}/orders/{order_id}")
            if resp.status_code == 404:
                return {"ok": False, "error": "订单不存在"}
            resp.raise_for_status()
            return {"ok": True, "data": resp.json()}


class ModifyOrderTool(BaseTool):
    name = "modify_order"
    description = "修改订单信息（地址/备注等）。修改前必须与客户确认。"
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号"},
            "fields": {"type": "object", "description": "需要修改的字段键值对"},
        },
        "required": ["order_id", "fields"],
        "additionalProperties": False,
    }
    timeout = 10
    base_url = "http://order-service:8080"

    async def run(self, order_id: str, fields: dict):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.patch(f"{self.base_url}/orders/{order_id}", json={"fields": fields})
            if resp.status_code == 404:
                return {"ok": False, "error": "订单不存在"}
            resp.raise_for_status()
            return {"ok": True, "data": resp.json()}
