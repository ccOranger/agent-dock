"""管理后台：人工接管（转人工队列 / 坐席接入 / 回复 / 释放）

会话状态机（与 chat_service 的让位契约配套）：
    active --(低置信 / escalate 工具)--> handover（排队待接入）
    handover --(坐席 claim)--> human_handling（机器人让位，客户消息只落库）
    human_handling --(release to=bot)--> active（机器人重新接管）
    human_handling --(release to=close)--> closed（会话结束）
"""
from fastapi import APIRouter, Depends, Query

from app.api.deps import require_staff
from app.api.params import parse_uuid
from app.core.constants import SessionStatus
from app.core.exceptions import Conflict, Forbidden, NotFound, ParamError
from app.core.security import sanitize_output
from app.models.repositories import MessageRepo, SceneRepo, SessionRepo, StaffRepo
from app.schemas.api import TakeoverMessageRequest, TakeoverReleaseRequest
from app.schemas.common import ApiResponse, PaginatedData

router = APIRouter(prefix="/admin/takeover", tags=["admin-takeover"])

# 队列/详情的状态过滤值别名：queued=等待接入，handling=已接入
_STATUS_ALIASES = {
    "queued": SessionStatus.HANDOVER,
    "handling": SessionStatus.HUMAN_HANDLING,
    "handover": SessionStatus.HANDOVER,
    "human_handling": SessionStatus.HUMAN_HANDLING,
}


def _resolve_status(value: str | None) -> str | None:
    if not value:
        return None
    status = _STATUS_ALIASES.get(value)
    if status is None:
        raise ParamError(f"非法的接管状态过滤: {value}")
    return status


async def _resolve_scene_id(scene_code: str | None) -> str | None:
    if not scene_code:
        return None
    scene = await SceneRepo.get_by_code(scene_code)
    if scene is None:
        raise ParamError(f"场景不存在: {scene_code}")
    return str(scene.id)


async def _require_handling(session_id: str, user) -> object:
    """claim 之外的写接口前置校验：会话存在、坐席已接入、操作者是接手人（admin 可调岗）。"""
    sess = await SessionRepo.get(session_id)
    if sess is None:
        raise NotFound("会话不存在")
    if sess.status != SessionStatus.HUMAN_HANDLING:
        raise Conflict("会话不在人工接管中（先接入或已被释放）")
    if user.role != "admin" and str(sess.assigned_agent) != str(user.id):
        raise Forbidden("该会话由其他坐席接入，仅接手人或管理员可操作")
    return sess


@router.get("/sessions", response_model=ApiResponse[PaginatedData[dict]])
async def list_takeover_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="queued(待接入) | handling(已接入)；缺省返回两者"),
    scene_code: str | None = None,
    user=Depends(require_staff),
):
    """待接入队列：排队最久的优先；带客户名、场景、接手坐席与消息数。"""
    scene_id = await _resolve_scene_id(scene_code)
    items, total = await SessionRepo.takeover_queue(
        scene_id=scene_id,
        status=_resolve_status(status),
        page=page,
        page_size=page_size,
    )
    return ApiResponse(
        data=PaginatedData(items=items, total=total, page=page, page_size=page_size)
    )


@router.get("/sessions/{session_id}", response_model=ApiResponse[dict])
async def get_takeover_session(
    session_id: str,
    after_id: str | None = Query(None, description="增量游标：只返回该消息 ID 之后的新消息"),
    user=Depends(require_staff),
):
    """接管详情：会话信息 + 消息列表。带 after_id 时走增量轮询（客户端按 id 去重）。"""
    sess = await SessionRepo.get(parse_uuid(session_id, "session_id"))
    if sess is None:
        raise NotFound("会话不存在")
    scene = await SceneRepo.get(str(sess.scene_id)) if sess.scene_id else None
    if after_id:
        messages = await MessageRepo.list_by_session_after(sess.id, after_id)
    else:
        messages = await MessageRepo.list_by_session(sess.id)
    assigned = None
    if sess.assigned_agent:
        staff = await StaffRepo.get_by_id(sess.assigned_agent)
        assigned = {
            "id": str(sess.assigned_agent),
            "name": (staff.display_name or staff.username) if staff else None,
        }
    return ApiResponse(
        data={
            "session": {
                "id": str(sess.id),
                "title": sess.title,
                "status": sess.status,
                "scene_code": scene.code if scene else None,
                "user_id": str(sess.user_id) if sess.user_id else None,
                "visitor_id": str(sess.visitor_id) if sess.visitor_id else None,
                "last_route_to": sess.last_route_to,
                "assigned_agent": assigned,
                "created_at": sess.created_at.isoformat(),
                "updated_at": sess.updated_at.isoformat(),
            },
            "messages": messages,
        }
    )


@router.post("/sessions/{session_id}/claim", response_model=ApiResponse[dict])
async def claim_takeover_session(
    session_id: str,
    user=Depends(require_staff),
):
    """坐席接入：handover → human_handling 并绑定 assigned_agent；同一坐席重复调用幂等。"""
    result = await SessionRepo.claim_takeover(parse_uuid(session_id, "session_id"), user.id)
    if result is None:
        raise NotFound("会话不存在")
    if result is True:
        raise Conflict("会话已被其他坐席接入或状态不允许接入")
    return ApiResponse(
        data={
            "session_id": str(result.id),
            "status": result.status,
            "assigned_agent_id": str(result.assigned_agent),
        }
    )


@router.post("/sessions/{session_id}/messages", response_model=ApiResponse[dict])
async def send_takeover_message(
    session_id: str,
    body: TakeoverMessageRequest,
    user=Depends(require_staff),
):
    """坐席回复：以 role=assistant + metadata.sender_type=staff 落库，客户端轮询可见。

    routed_to 固定为 "staff"：既供前端区分人工/机器人气泡，也让机器人上下文
    （MessageRepo.list_recent）将其排除，避免模型复述坐席话术。
    """
    content = body.content.strip()
    if not content:
        raise ParamError("回复内容不能为空")
    sess = await _require_handling(parse_uuid(session_id, "session_id"), user)
    msg = await MessageRepo.add(
        sess.id,
        role="assistant",
        content=sanitize_output(content),
        routed_to="staff",
        metadata={"sender_type": "staff", "staff_id": str(user.id)},
    )
    return ApiResponse(
        data={
            "message_id": str(msg.id),
            "session_id": str(sess.id),
            "created_at": msg.created_at.isoformat(),
        }
    )


@router.post("/sessions/{session_id}/release", response_model=ApiResponse[dict])
async def release_takeover_session(
    session_id: str,
    body: TakeoverReleaseRequest | None = None,
    user=Depends(require_staff),
):
    """结束人工：to=bot 交还机器人（active），to=close 结束会话（closed）。"""
    to_status = (body.to if body else "bot") or "bot"
    target = SessionStatus.ACTIVE if to_status == "bot" else SessionStatus.CLOSED
    ok = await SessionRepo.release_takeover(
        parse_uuid(session_id, "session_id"), user.id, to_status=target
    )
    if ok is None:
        raise NotFound("会话不存在")
    if not ok:
        raise Conflict("会话不在人工接管中（先接入或已被释放）")
    return ApiResponse(data={"session_id": session_id, "status": str(target)})
