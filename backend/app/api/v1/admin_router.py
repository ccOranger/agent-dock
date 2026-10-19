"""管理后台：路由分发器（只读）

路由 Agent 与业务 Agent 不同：它的定义（提示词模板、模型、意图上限、兜底策略）在代码 + .env 里，
不在 agents 表中，因此本模块**只提供查看**，不提供任何写接口。
要改这些参数请在 backend/.env 调整后重启（详见 docs/runbook.md）。

可看的两件事：
- 基本档案：职责、生效参数、提示词模板与占位符含义
- 生效提示词：按当前 Agent 库实时渲染出的、真正会发给模型的那段提示词，
  以及每个候选 Agent 拿到了多少路由线索（关键词/典型问法是否填全，被截断了几条）
"""
from fastapi import APIRouter, Depends

from app.agents.router_agent import describe_router_agent, render_router_prompt
from app.api.deps import require_admin
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/admin/router", tags=["admin-router"])


@router.get("", response_model=ApiResponse[dict])
async def router_info(user=Depends(require_admin)):
    """路由分发器基本档案（含提示词模板原文与占位符说明，均为只读）。"""
    return ApiResponse(data=describe_router_agent())


@router.get("/prompt", response_model=ApiResponse[dict])
async def router_effective_prompt(user=Depends(require_admin)):
    """当前生效的路由提示词预览与候选 Agent 清单（每次调用按库中现状实时渲染）。"""
    return ApiResponse(data=await render_router_prompt())
