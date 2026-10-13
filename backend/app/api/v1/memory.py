"""记忆接口（包装 MemoryTool + DB 归档查询）"""
from fastapi import APIRouter, Depends, Query

from app.api.deps import current_user, require_staff
from app.core.exceptions import NotFound
from app.models.repositories import MemoryRepo
from app.schemas.api import MemorySearchRequest, MemoryUpdateRequest
from app.schemas.common import ApiResponse
from app.services.memory_service import MemoryService

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/me", response_model=ApiResponse[list])
async def my_memories(type: str | None = None, user=Depends(current_user)):
    rows = await MemoryRepo.list_by_user(user.id, type)
    return ApiResponse(
        data=[
            {
                "id": str(r.id),
                "memory_uid": r.memory_uid,
                "type": r.type,
                "content": r.content,
                "importance": r.importance,
                "access_count": r.access_count,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    )


@router.get("/users/{user_id}", response_model=ApiResponse[list])
async def user_memories(user_id: str, type: str | None = None, user=Depends(require_staff)):
    rows = await MemoryRepo.list_by_user(user_id, type)
    return ApiResponse(
        data=[
            {
                "id": str(r.id),
                "memory_uid": r.memory_uid,
                "type": r.type,
                "content": r.content,
                "importance": r.importance,
                "access_count": r.access_count,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    )


@router.post("/search", response_model=ApiResponse)
async def search_memories(body: MemorySearchRequest, user=Depends(require_staff)):
    service = MemoryService()
    target_user = body.user_id or str(user.id)
    result = await service.search(target_user, body.query, body.limit)
    return ApiResponse(data={"results": result})


@router.patch("/{memory_id}", response_model=ApiResponse)
async def update_memory(memory_id: str, body: MemoryUpdateRequest, user=Depends(require_staff)):
    target = await MemoryRepo.get(memory_id)
    if target is None:
        raise NotFound("记忆不存在")
    service = MemoryService()
    await service.update(str(target.user_id), target.memory_uid, body.content, body.importance)
    if body.content is not None:
        from app.models.repositories import MemoryRepo as MR

        await MR.upsert_mirror(
            user_id=target.user_id,
            memory_uid=target.memory_uid,
            type_=target.type,
            content=body.content,
            importance=body.importance if body.importance is not None else target.importance,
        )
    return ApiResponse()


@router.delete("/{memory_id}", response_model=ApiResponse)
async def forget_memory(memory_id: str, user=Depends(require_staff)):
    target = await MemoryRepo.get(memory_id)
    if target is None:
        raise NotFound("记忆不存在")
    service = MemoryService()
    await service.forget(str(target.user_id), target.memory_uid)
    await MemoryRepo.delete(memory_id)
    return ApiResponse()
