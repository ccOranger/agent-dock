"""管理后台：客诉工单管理（结构化增改查，读写独立 tickets 表）

工单由投诉 Agent 通过 create_ticket/update_ticket 工具落库，后台在此按状态/客户/
分类/紧急度过滤查看，并可流转状态、补录备注。写操作以 ticket_no 定位。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_staff
from app.core.exceptions import NotFound, ParamError
from app.models.repositories import TicketRepo
from app.schemas.api import TicketNoteCreate, TicketStatusUpdate
from app.schemas.common import ApiResponse, PaginatedData

router = APIRouter(prefix="/admin/tickets", tags=["admin-tickets"])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ParamError(f"时间格式非法: {value}")


@router.get("", response_model=ApiResponse[PaginatedData[dict]])
async def list_tickets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: str | None = None,
    status: str | None = Query(None, description="open | in_progress | escalated | resolved | closed"),
    category: str | None = None,
    urgency: str | None = Query(None, description="low | medium | high"),
    keyword: str | None = Query(None, description="按工单号/内容模糊搜索"),
    date_from: str | None = None,
    date_to: str | None = None,
    user=Depends(require_staff),
):
    """工单列表：支持按客户/状态/分类/紧急度/关键字/时间范围过滤。"""
    items, total = await TicketRepo.list_filtered(
        user_id=user_id,
        status=status,
        category=category,
        urgency=urgency,
        keyword=keyword,
        date_from=_parse_dt(date_from),
        date_to=_parse_dt(date_to),
        page=page,
        page_size=page_size,
    )
    return ApiResponse(
        data=PaginatedData(items=items, total=total, page=page, page_size=page_size)
    )


@router.get("/{ticket_no}", response_model=ApiResponse[dict])
async def get_ticket(ticket_no: str, user=Depends(require_staff)):
    """工单详情（含补录历史 updates）。"""
    row = await TicketRepo.get_by_ticket_no(ticket_no)
    if row is None:
        raise NotFound("工单不存在")
    return ApiResponse(data=TicketRepo.to_dict(row, include_updates=True))


@router.patch("/{ticket_no}/status", response_model=ApiResponse[dict])
async def update_ticket_status(
    ticket_no: str, body: TicketStatusUpdate, user=Depends(require_staff)
):
    """流转工单状态，可附带一条备注（写入 updates 历史）。"""
    row = await TicketRepo.set_status(ticket_no, body.status, note=body.note)
    if row is None:
        raise NotFound("工单不存在")
    return ApiResponse(data=TicketRepo.to_dict(row))


@router.post("/{ticket_no}/notes", response_model=ApiResponse[dict])
async def add_ticket_note(ticket_no: str, body: TicketNoteCreate, user=Depends(require_staff)):
    """向工单补录一条信息（与业务 Agent 的 update_ticket 走同一存储结构）。"""
    row = await TicketRepo.append_update(ticket_no, body.content)
    if row is None:
        raise NotFound("工单不存在")
    return ApiResponse(data=TicketRepo.to_dict(row))
