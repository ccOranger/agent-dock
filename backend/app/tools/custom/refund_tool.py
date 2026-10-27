"""退款工具（进程内自定义版本，SOP 示例业务使用）。

历史版本访问固定假域名 http://refund-service:8080（不存在）。现改为读写共享
固定数据集 mock_data；同一份退款数据也被本地 MCP 服务 mcp_refund 使用。
"""
from app.tools.custom import mock_data
from app.tools.custom.base import BaseTool


class SubmitRefundTool(BaseTool):
    name = "submit_refund"
    description = "提交退款申请，返回退款单号。禁止直接承诺退款金额。"
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号，例如 SO20240001"},
            "reason": {"type": "string", "description": "退款原因"},
        },
        "required": ["order_id", "reason"],
        "additionalProperties": False,
    }

    async def run(self, order_id: str, reason: str):
        refund, error = mock_data.submit_refund(order_id, reason)
        if error:
            return {"ok": False, "error": error}
        return {"ok": True, "data": refund}


class QueryRefundTool(BaseTool):
    name = "query_refund"
    description = "根据订单号查询退款进度。"
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号"},
        },
        "required": ["order_id"],
        "additionalProperties": False,
    }

    async def run(self, order_id: str):
        refund = mock_data.get_refund_by_order(order_id)
        if refund is None:
            return {"ok": False, "error": "未查询到该订单的退款记录"}
        return {"ok": True, "data": refund}
