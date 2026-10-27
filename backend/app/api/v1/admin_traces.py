"""管理后台：请求链路追踪（只读）

一轮对话 = 一条 trace：经过哪些业务 Agent、绑了哪些 MCP、实际调了哪些工具、
RAG/记忆命中多少、耗时与结局。明细时间线在 detail 的 spans 中。
"""
from fastapi import APIRouter, Depends, Query

from app.api.deps import require_staff
from app.api.params import normalize_trace_id, parse_uuid, split_session_ref
from app.core.exceptions import NotFound
from app.models.repositories import TraceRepo
from app.schemas.common import ApiResponse, PaginatedData

router = APIRouter(prefix="/admin/traces", tags=["admin-traces"])


def _parse_dt(value: str | None):
    if not value:
        return None
    from datetime import datetime

    from app.core.exceptions import ParamError

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ParamError(f"时间格式非法: {value}") from None


@router.get("", response_model=ApiResponse[PaginatedData[dict]])
async def list_traces(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session_id: str | None = Query(None, description="会话 ID（UUID）；粘 16 位链路 ID 也可）"),
    trace_id: str | None = Query(None, description="HTTP 链路 ID（16 位十六进制）"),
    user_id: str | None = None,
    scene_code: str | None = Query(None, description="按业务场景过滤（如 default / mall_order）"),
    agent_name: str | None = None,
    tool_name: str | None = None,
    mcp_server: str | None = None,
    outcome: str | None = Query(None, description="answered | handover | error"),
    has_error: bool = False,
    min_latency_ms: int | None = Query(None, ge=0),
    date_from: str | None = None,
    date_to: str | None = None,
    user=Depends(require_staff),
):
    """链路列表（不带 spans 明细）：支持按场景/客户/会话/链路/Agent/工具/MCP/结局/耗时/时间过滤。"""
    session_ref, ref_trace_id = split_session_ref(session_id)
    items, total = await TraceRepo.list_filtered(
        session_id=session_ref,
        trace_id=normalize_trace_id(trace_id) or ref_trace_id,
        user_id=parse_uuid(user_id, "user_id"),
        scene_code=scene_code,
        agent_name=agent_name,
        tool_name=tool_name,
        mcp_server=mcp_server,
        outcome=outcome,
        has_error=has_error,
        min_latency_ms=min_latency_ms,
        date_from=_parse_dt(date_from),
        date_to=_parse_dt(date_to),
        page=page,
        page_size=page_size,
    )
    return ApiResponse(
        data=PaginatedData(items=items, total=total, page=page, page_size=page_size)
    )


@router.get("/usage", response_model=ApiResponse[list])
async def trace_usage(
    field: str = Query("tool_names", pattern="^(tool_names|agent_names|mcp_server_names)$"),
    days: int = Query(7, ge=1, le=90),
    user=Depends(require_staff),
):
    """近 N 天工具 / Agent / MCP 服务被多少轮请求覆盖（用于识别没人用或长期失败的依赖）。"""
    return ApiResponse(data=await TraceRepo.usage_counts(field=field, days=days))


@router.get("/sessions/{session_id}", response_model=ApiResponse[list])
async def list_session_traces(session_id: str, user=Depends(require_staff)):
    """某会话按轮次的链路摘要（时间正序），用于画会话级链路串。"""
    return ApiResponse(data=await TraceRepo.list_by_session(parse_uuid(session_id, "session_id")))


@router.get("/{trace_ref}", response_model=ApiResponse[dict])
async def get_trace(trace_ref: str, user=Depends(require_staff)):
    """单条链路详情（含 spans 时间线）。

    trace_ref 可传：行 ID(UUID) / HTTP trace_id / 助手消息 ID，便于从咨询记录直接跳过来。
    """
    data = await TraceRepo.detail(trace_ref)
    if data is None:
        raise NotFound("链路记录不存在")
    return ApiResponse(data=data)
