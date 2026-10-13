"""管理接口：Agent / 工具 / 指标"""
from fastapi import APIRouter, Depends
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.agents.registry import list_agents
from app.api.deps import require_admin, require_staff
from app.core.exceptions import NotFound
from app.models.repositories import AgentRepo, ToolRepo
from app.schemas.api import AgentToggleRequest
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/agents", response_model=ApiResponse[list])
async def agents(user=Depends(require_admin)):
    """代码注册表 + DB 状态合并"""
    db_rows = {r.name: r for r in await AgentRepo.list_all()}
    result = []
    for item in list_agents():
        meta = db_rows.get(item["name"])
        result.append(
            {
                **item,
                "enabled": meta.enabled if meta else True,
                "display_name": meta.display_name if meta else item["name"],
            }
        )
    return ApiResponse(data=result)


@router.get("/agents/{name}", response_model=ApiResponse[dict])
async def agent_detail(name: str, user=Depends(require_admin)):
    for item in list_agents():
        if item["name"] == name:
            meta = await AgentRepo.get_by_name(name)
            return ApiResponse(data={**item, "enabled": meta.enabled if meta else True})
    raise NotFound("Agent 不存在")


@router.patch("/agents/{name}/toggle", response_model=ApiResponse)
async def toggle_agent(name: str, body: AgentToggleRequest, user=Depends(require_admin)):
    meta = await AgentRepo.get_by_name(name)
    if meta is None:
        raise NotFound("Agent 未登记")
    await AgentRepo.set_enabled(name, body.enabled)
    return ApiResponse()


@router.get("/tools", response_model=ApiResponse[list])
async def tools(user=Depends(require_staff)):
    rows = await ToolRepo.list_all()
    return ApiResponse(
        data=[
            {"id": str(r.id), "name": r.name, "type": r.type, "enabled": r.enabled}
            for r in rows
        ]
    )


@router.get("/metrics")
async def metrics(user=Depends(require_admin)):
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
