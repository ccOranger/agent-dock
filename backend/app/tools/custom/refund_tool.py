"""退款工具（SOP 示例业务 RefundAgent 使用）"""
import httpx

from app.tools.custom.base import BaseTool


class SubmitRefundTool(BaseTool):
    name = "submit_refund"
    description = "提交退款申请，返回退款单号。禁止直接承诺退款金额。"
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号"},
            "reason": {"type": "string", "description": "退款原因"},
        },
        "required": ["order_id", "reason"],
        "additionalProperties": False,
    }
    timeout = 10
    base_url = "http://refund-service:8080"

    async def run(self, order_id: str, reason: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/refunds", json={"order_id": order_id, "reason": reason}
            )
            resp.raise_for_status()
            return {"ok": True, "data": resp.json()}
