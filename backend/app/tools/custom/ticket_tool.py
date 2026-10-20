"""工单工具：创建工单 / 补充更新工单 / 升级人工"""
import uuid as _uuid

from app.tools.custom.base import BaseTool


def _as_uuid(value):
    """尽量把值转成 UUID；非 UUID（如示例客户名 alice）返回 None，不阻断写入。"""
    if not value:
        return None
    try:
        return _uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


class CreateTicketTool(BaseTool):
    name = "create_ticket"
    description = (
        "创建客诉工单并返回工单号，识别到投诉时必须立即调用。"
        "调用格式 [TOOL_CALL:create_ticket:content=投诉概括]；"
        "参数：content=投诉内容(必填，信息不全就写“...（待补充）”，不要因缺信息而不建单)；"
        "category=投诉分类(可选，如 logistics/product/service)；urgency=low/medium/high(可选)。"
        "user_id/session_id 由系统自动注入，无需填写。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "客户 ID"},
            "session_id": {"type": "string", "description": "会话 ID"},
            "category": {"type": "string", "description": "投诉分类"},
            "content": {"type": "string", "description": "投诉内容（5W1H）"},
            "urgency": {"type": "string", "enum": ["low", "medium", "high"], "description": "紧急度"},
        },
        "required": ["user_id", "content"],
        "additionalProperties": False,
    }
    timeout = 10

    async def run(
        self,
        user_id: str,
        content: str,
        session_id: str = "",
        category: str = "general",
        urgency: str = "medium",
    ):
        from app.models.repositories import TicketRepo

        # 工单落到独立 tickets 表（不再是 audit_logs）：结构化存储、可按状态流转与回查
        row = await TicketRepo.create(
            content=content,
            user_id=user_id,
            session_id=session_id,
            category=category,
            urgency=urgency,
        )
        return {"ok": True, "data": {"ticket_id": row.ticket_no, "status": "created"}}


class UpdateTicketTool(BaseTool):
    name = "update_ticket"
    description = (
        "向已存在的工单补充/更新信息。工单创建后客户补充订单号、快递公司、地址等细节时必须调用。"
        "调用格式 [TOOL_CALL:update_ticket:ticket_id=工单号,content=补充内容]；"
        "参数：ticket_id=工单号(必填，形如 TK-XXXXXXXX，取自之前创建的工单号)；content=本次补充或更新的内容(必填)。"
        "user_id/session_id 由系统自动注入。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "客户 ID"},
            "session_id": {"type": "string", "description": "会话 ID"},
            "ticket_id": {"type": "string", "description": "工单号，形如 TK-XXXXXXXX"},
            "content": {"type": "string", "description": "本次补充或更新的内容"},
        },
        "required": ["ticket_id", "content"],
        "additionalProperties": False,
    }
    timeout = 10

    async def run(self, ticket_id: str, content: str, user_id: str = "", session_id: str = ""):
        from app.models.repositories import TicketRepo

        # 直接向 tickets 表对应工单追加一条更新（结构化补录，可按工单号回查历史）
        row = await TicketRepo.append_update(ticket_id, content)
        if row is None:
            return {"ok": False, "data": {"ticket_id": ticket_id, "found": False, "error": "工单不存在"}}
        return {"ok": True, "data": {"ticket_id": row.ticket_no, "status": row.status, "updated": True}}


class EscalateTool(BaseTool):
    name = "escalate"
    description = (
        "将当前会话升级转人工处理，客户情绪激动或问题严重时调用。"
        "调用格式 [TOOL_CALL:escalate:reason=升级原因]；"
        "参数：reason=升级原因(必填)。session_id/user_id 由系统自动注入。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "客户 ID"},
            "session_id": {"type": "string", "description": "会话 ID"},
            "reason": {"type": "string", "description": "升级原因"},
        },
        "required": ["session_id", "reason"],
        "additionalProperties": False,
    }
    timeout = 10

    async def run(self, session_id: str, reason: str, user_id: str = ""):
        from app.models.repositories import AuditRepo, TicketRepo

        # 工单升级状态落到 tickets 表（若该客户/会话有未关闭工单）
        ticket_no = None
        ticket = await TicketRepo.latest_open_for(user_id=user_id, session_id=session_id)
        if ticket is not None:
            await TicketRepo.set_status(ticket.ticket_no, "escalated", note=f"升级人工：{reason}")
            ticket_no = ticket.ticket_no

        # 转人工是会话级安全事件，仍保留一条审计（不属于工单数据）
        await AuditRepo.write(
            action="escalate",
            user_id=_as_uuid(user_id),
            session_id=_as_uuid(session_id),
            target_type="session",
            target_id=str(session_id) if session_id else None,
            payload={"reason": reason, "user_id": user_id, "ticket_no": ticket_no},
        )
        return {"ok": True, "data": {"session_id": session_id, "handover": True, "ticket_id": ticket_no}}
