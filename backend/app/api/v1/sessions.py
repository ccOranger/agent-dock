"""会话与历史接口"""
import asyncio
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_customer
from app.core.exceptions import Forbidden, NotFound
from app.models.entities import Session
from app.models.repositories import MessageRepo, SessionRepo
from app.schemas.api import SessionCreate, SessionOut, SessionUpdate
from app.schemas.common import ApiResponse, PaginatedData
from app.services.scene_service import load_scene_context

router = APIRouter(prefix="/sessions", tags=["sessions"])


async def _resolve_scene_id(scene_code: str | None) -> str:
    """会话接口按场景隔离：缺省落默认场景；未知场景返回 3003。

    不带 require_active：场景停用后客户仍应能查看/删除自己的历史会话。
    """
    context = await load_scene_context(scene_code, require_active=False)
    return context.scene_id


def _to_out(s) -> SessionOut:
    return SessionOut(
        id=str(s.id),
        title=s.title,
        status=s.status,
        last_route_to=s.last_route_to,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


async def _get_owned(
    session_id: str, user, db: AsyncSession | None = None, scene_id: str | None = None
) -> Session:
    # 传入 db 时在当前事务内查询：后续修改/删除直接作用于受管对象，避免 detached ORM 回写
    sess = await SessionRepo.get(session_id, db=db)
    # 跨场景不区分「不存在 / 不属于你」：都不该泄露另一个场景的会话存在性
    if sess is None or (scene_id is not None and str(sess.scene_id) != str(scene_id)):
        raise NotFound("会话不存在")
    if str(sess.user_id) != str(user.id):
        raise Forbidden()
    return sess


@router.get("", response_model=ApiResponse[PaginatedData[SessionOut]])
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    scene_code: str | None = Query(None, max_length=32),
    user=Depends(current_customer),
):
    scene_id = await _resolve_scene_id(scene_code)
    items, total = await SessionRepo.list_by_user(
        user.id, page, page_size, scene_id=scene_id
    )
    return ApiResponse(
        data=PaginatedData(
            items=[_to_out(s) for s in items], total=total, page=page, page_size=page_size
        )
    )


@router.post("", response_model=ApiResponse[SessionOut])
async def create_session(body: SessionCreate, user=Depends(current_customer)):
    scene_id = await _resolve_scene_id(body.scene_code)
    sess = await SessionRepo.create(user.id, title=body.title, scene_id=scene_id)
    return ApiResponse(data=_to_out(sess))


@router.get("/{session_id}", response_model=ApiResponse[SessionOut])
async def get_session(
    session_id: uuid.UUID,
    scene_code: str | None = Query(None, max_length=32),
    user=Depends(current_customer),
):
    sess = await _get_owned(str(session_id), user, scene_id=await _resolve_scene_id(scene_code))
    return ApiResponse(data=_to_out(sess))


@router.patch("/{session_id}", response_model=ApiResponse[SessionOut])
async def update_session(
    session_id: uuid.UUID,
    body: SessionUpdate,
    scene_code: str | None = Query(None, max_length=32),
    user=Depends(current_customer),
):
    from app.core.database import SessionLocal

    scene_id = await _resolve_scene_id(scene_code)
    async with SessionLocal() as db:
        sess = await _get_owned(str(session_id), user, db, scene_id)
        prev_status = sess.status
        if body.title is not None:
            sess.title = body.title
        if body.status is not None:
            sess.status = body.status
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
async def delete_session(
    session_id: uuid.UUID,
    scene_code: str | None = Query(None, max_length=32),
    user=Depends(current_customer),
):
    from app.core.database import SessionLocal

    scene_id = await _resolve_scene_id(scene_code)
    async with SessionLocal() as db:
        sess = await _get_owned(str(session_id), user, db, scene_id)
        await db.delete(sess)
        await db.commit()
    return ApiResponse()


@router.get("/{session_id}/messages", response_model=ApiResponse[PaginatedData[dict]])
async def list_messages(
    session_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    scene_code: str | None = Query(None, max_length=32),
    user=Depends(current_customer),
):
    await _get_owned(str(session_id), user, scene_id=await _resolve_scene_id(scene_code))
    items = await MessageRepo.list_by_session(session_id, page=page, page_size=page_size)
    total = await MessageRepo.count_by_session(session_id)
    return ApiResponse(
        data=PaginatedData(items=items, total=total, page=page, page_size=page_size)
    )
