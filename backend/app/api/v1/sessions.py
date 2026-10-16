"""会话与历史接口"""
import asyncio
import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import current_customer
from app.core.exceptions import Forbidden, NotFound
from app.models.repositories import MessageRepo, SessionRepo
from app.schemas.api import SessionCreate, SessionOut, SessionUpdate
from app.schemas.common import ApiResponse, PaginatedData

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _to_out(s) -> SessionOut:
    return SessionOut(
        id=str(s.id),
        title=s.title,
        status=s.status,
        last_route_to=s.last_route_to,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


async def _get_owned(session_id: str, user) -> "SessionRepo":
    sess = await SessionRepo.get(session_id)
    if sess is None:
        raise NotFound("会话不存在")
    if str(sess.user_id) != str(user.id):
        raise Forbidden()
    return sess


@router.get("", response_model=ApiResponse[PaginatedData[SessionOut]])
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user=Depends(current_customer),
):
    items, total = await SessionRepo.list_by_user(user.id, page, page_size)
    return ApiResponse(
        data=PaginatedData(
            items=[_to_out(s) for s in items], total=total, page=page, page_size=page_size
        )
    )


@router.post("", response_model=ApiResponse[SessionOut])
async def create_session(body: SessionCreate, user=Depends(current_customer)):
    sess = await SessionRepo.create(user.id, title=body.title)
    return ApiResponse(data=_to_out(sess))


@router.get("/{session_id}", response_model=ApiResponse[SessionOut])
async def get_session(session_id: uuid.UUID, user=Depends(current_customer)):
    sess = await _get_owned(str(session_id), user)
    return ApiResponse(data=_to_out(sess))


@router.patch("/{session_id}", response_model=ApiResponse[SessionOut])
async def update_session(session_id: uuid.UUID, body: SessionUpdate, user=Depends(current_customer)):
    sess = await _get_owned(str(session_id), user)
    prev_status = sess.status
    if body.title is not None:
        sess.title = body.title
    if body.status is not None:
        sess.status = body.status
    from app.core.database import SessionLocal

    async with SessionLocal() as db:
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
    if body.status == "closed" and prev_status != "closed":
        # spec 8.6：会话结束全量晋升 working → 长期，后台执行不阻塞响应
        from app.core.context_engineer import _BG_TASKS, ContextEngineer

        task = asyncio.create_task(
            ContextEngineer().promote_on_session_close(str(user.id), str(session_id))
        )
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)
    return ApiResponse(data=_to_out(sess))


@router.delete("/{session_id}", response_model=ApiResponse)
async def delete_session(session_id: uuid.UUID, user=Depends(current_customer)):
    sess = await _get_owned(str(session_id), user)
    from app.core.database import SessionLocal

    async with SessionLocal() as db:
        await db.delete(sess)
        await db.commit()
    return ApiResponse()


@router.get("/{session_id}/messages", response_model=ApiResponse[PaginatedData[dict]])
async def list_messages(
    session_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user=Depends(current_customer),
):
    await _get_owned(str(session_id), user)
    items = await MessageRepo.list_by_session(session_id, page=page, page_size=page_size)
    return ApiResponse(
        data=PaginatedData(items=items, total=len(items), page=page, page_size=page_size)
    )
