"""工单工具：创建工单 / 升级人工"""
from app.tools.custom.base import BaseTool


class CreateTicketTool(BaseTool):
    name = "create_ticket"
    description = "创建客诉工单，返回工单号。投诉受理时必须调用。"
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "客户 ID"},
            "category": {"type": "string", "description": "投诉分类"},
            "content": {"type": "string", "description": "投诉内容（5W1H）"},
            "urgency": {"type": "string", "enum": ["low", "medium", "high"], "description": "紧急度"},
        },
        "required": ["user_id", "content"],
        "additionalProperties": False,
    }
    timeout = 10

    async def run(self, user_id: str, content: str, category: str = "general", urgency: str = "medium"):
        import uuid

        from app.models.repositories import AuditRepo

        ticket_id = f"TK-{uuid.uuid4().hex[:8].upper()}"
        await AuditRepo.write(
            action="create_ticket",
            payload={"ticket_id": ticket_id, "user_id": user_id, "category": category, "urgency": urgency, "content": content[:500]},
        )
        return {"ok": True, "data": {"ticket_id": ticket_id, "status": "created"}}


class EscalateTool(BaseTool):
    name = "escalate"
    description = "将当前会话升级转人工处理。客户情绪激动或问题严重时调用。"
    parameters = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "会话 ID"},
            "reason": {"type": "string", "description": "升级原因"},
        },
        "required": ["session_id", "reason"],
        "additionalProperties": False,
    }
    timeout = 10

    async def run(self, session_id: str, reason: str):
        from app.models.repositories import AuditRepo

        await AuditRepo.write(action="escalate", session_id=session_id, payload={"reason": reason})
        return {"ok": True, "data": {"session_id": session_id, "handover": True}}
