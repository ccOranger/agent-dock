"""物流查询工具"""
import httpx

from app.tools.custom.base import BaseTool


class QueryLogisticsTool(BaseTool):
    name = "query_logistics"
    description = "根据订单号查询物流轨迹。"
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号"},
        },
        "required": ["order_id"],
        "additionalProperties": False,
    }
    timeout = 10
    base_url = "http://logistics-service:8080"

    async def run(self, order_id: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}/logistics/{order_id}")
            if resp.status_code == 404:
                return {"ok": False, "error": "无物流记录"}
            resp.raise_for_status()
            return {"ok": True, "data": resp.json()}
