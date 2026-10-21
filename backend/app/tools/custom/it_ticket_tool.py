"""IT 工单工具：创建工单 / 补充更新工单 / 升级人工

复用平台 `tickets` 表与 `TicketRepo`，工具名独立（create_it_ticket 等），
并在 metadata 中标记 source=it_helpdesk，保留业务来源。
"""
import uuid as _uuid

from app.tools.custom.base import BaseTool


def _as_uuid(value):
    """尽量把值转成 UUID；非 UUID（如示例员工名 zhangsan）返回 None，不阻断写入。"""
    if not value:
        return None
    try:
        return _uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


class CreateItTicketTool(BaseTool):
    name = "create_it_ticket"
    description = (
        "创建 IT 故障工单并返回工单号，识别到故障描述时必须立即调用。"
        "调用格式 [TOOL_CALL:create_it_ticket:content=故障概括]；"
        "参数：content=故障现象(必填，信息不全就写“...（待补充）”，不要因缺信息而不建单)；"
        "category=it_hardware/it_network/it_account/it_software/it_security/it_other(可选)；"
        "urgency=low/medium/high(可选)。user_id/session_id 由系统自动注入，无需填写。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "员工 ID"},
            "session_id": {"type": "string", "description": "会话 ID"},
            "category": {
                "type": "string",
                "enum": [
                    "it_hardware",
                    "it_network",
                    "it_account",
                    "it_software",
                    "it_security",
                    "it_other",
                ],
                "description": "故障分类",
            },
            "content": {"type": "string", "description": "故障现象概述"},
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
        category: str = "it_other",
        urgency: str = "medium",
    ):
        from app.models.repositories import TicketRepo

        row = await TicketRepo.create(
            content=content,
            user_id=user_id,
            session_id=session_id,
            category=category,
            urgency=urgency,
            metadata_={"source": "it_helpdesk"},
        )
        return {"ok": True, "data": {"ticket_id": row.ticket_no, "status": "created"}}


class UpdateItTicketTool(BaseTool):
    name = "update_it_ticket"
    description = (
        "向已存在的 IT 工单补充/更新信息。工单创建后用户补充设备编号、报错信息、影响范围等细节时必须调用。"
        "调用格式 [TOOL_CALL:update_it_ticket:ticket_id=工单号,content=补充内容]；"
        "参数：ticket_id=工单号(必填，形如 TK-XXXXXXXX，取自之前创建的工单号)；content=本次补充或更新的内容(必填)。"
        "user_id/session_id 由系统自动注入。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "员工 ID"},
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

        row = await TicketRepo.append_update(ticket_id, content)
        if row is None:
            return {"ok": False, "data": {"ticket_id": ticket_id, "found": False, "error": "工单不存在"}}
        return {"ok": True, "data": {"ticket_id": row.ticket_no, "status": row.status, "updated": True}}


class EscalateItTool(BaseTool):
    name = "escalate_it"
    description = (
        "将当前会话升级 IT 人工处理，出现大面积断网、服务器不可用、数据泄露或账号被盗时调用。"
        "调用格式 [TOOL_CALL:escalate_it:reason=升级原因]；"
        "参数：reason=升级原因(必填)。session_id/user_id 由系统自动注入。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "员工 ID"},
            "session_id": {"type": "string", "description": "会话 ID"},
            "reason": {"type": "string", "description": "升级原因"},
        },
        "required": ["session_id", "reason"],
        "additionalProperties": False,
    }
    timeout = 10

    async def run(self, session_id: str, reason: str, user_id: str = ""):
        from app.models.repositories import AuditRepo, TicketRepo

        # 工单升级状态落到 tickets 表（若该员工/会话有未关闭工单）
        ticket_no = None
        ticket = await TicketRepo.latest_open_for(user_id=user_id, session_id=session_id)
        if ticket is not None:
            await TicketRepo.set_status(ticket.ticket_no, "escalated", note=f"升级 IT 人工：{reason}")
            ticket_no = ticket.ticket_no

        await AuditRepo.write(
            action="escalate",
            user_id=_as_uuid(user_id),
            session_id=_as_uuid(session_id),
            target_type="session",
            target_id=str(session_id) if session_id else None,
            payload={"source": "it_helpdesk", "reason": reason, "user_id": user_id, "ticket_no": ticket_no},
        )
        return {"ok": True, "data": {"session_id": session_id, "handover": True, "ticket_id": ticket_no}}
