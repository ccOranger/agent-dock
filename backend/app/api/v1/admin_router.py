"""后台路由查看：场景路由档案（派生视图）、提示词预览、试运行与统计。

路由 Agent 的“内核”（模板、模型、意图上限）在代码 + .env 里，
场景级的“路由配置”（候选池、阈值、兜底、补充规则）在 scenes 表里。
本模块把它们按场景组装成只读档案：

- 同一个 ``build_scene_router_profile`` 既服务运行时路由，也服务这里，
  因此后台看到的候选、参数、提示词与真正发出去的一致；
- 不提供修改接口：要改全局默认请改 backend/.env 并重启，要改场景配置请走场景管理。

旧路径 ``GET /admin/router`` 与 ``/admin/router/prompt`` 保留为全局视图
（不限定场景），至少兼容一个版本。
"""
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.agents.router_agent import RouterAgent, describe_router_agent, render_router_prompt
from app.agents.scene_router import (
    build_scene_router_profile,
    profile_summary,
    profile_to_dict,
)
from app.api.deps import require_admin
from app.core.exceptions import ParamError, SceneNotConfigured, SceneNotFound
from app.models.repositories import SceneRepo, TraceRepo
from app.schemas.common import ApiResponse
from app.schemas.scene import RouterEffectiveMode
from app.services.scene_service import load_scene_context

router = APIRouter(prefix="/admin/router", tags=["admin-router"])


class RouterTestRequest(BaseModel):
    """试运行入参：只带这一轮的消息与可选历史，不落会话、不写链路。"""

    message: str
    history: list[dict] | None = None


@router.get("", response_model=ApiResponse[dict])
async def router_info(user=Depends(require_admin)):
    """兼容旧路径：路由分发器全局档案（等同 /admin/router/template）。"""
    return ApiResponse(data=describe_router_agent())


@router.get("/template", response_model=ApiResponse[dict])
async def router_template(user=Depends(require_admin)):
    """全局模板、占位符、常量与 env 默认值（不区分场景）。"""
    return ApiResponse(data=describe_router_agent())


@router.get("/prompt", response_model=ApiResponse[dict])
async def router_effective_prompt(user=Depends(require_admin)):
    """兼容旧路径：全局视图的生效提示词（候选=全部生效 Agent，不限定场景）。"""
    return ApiResponse(data=await render_router_prompt())


@router.get("/scenes", response_model=ApiResponse[list])
async def router_scenes(user=Depends(require_admin)):
    """场景路由摘要列表：只给结论与警告，不渲染提示词正文。"""
    summaries = []
    for scene in await SceneRepo.list_all():
        context = await load_scene_context(scene.code, require_active=False)
        summaries.append(profile_summary(build_scene_router_profile(context)))
    return ApiResponse(data=summaries)


@router.get("/scenes/{code}", response_model=ApiResponse[dict])
async def router_scene_detail(code: str, user=Depends(require_admin)):
    """单个场景的生效配置 + 候选明细 + 健康警告（提示词正文走 /prompt）。"""
    context = await load_scene_context(code, require_active=False)
    return ApiResponse(data=profile_to_dict(build_scene_router_profile(context)))


@router.get("/scenes/{code}/prompt", response_model=ApiResponse[dict])
async def router_scene_prompt(code: str, user=Depends(require_admin)):
    """该场景真正会发给路由模型的那段系统提示词（无对话上下文基准版）。"""
    context = await load_scene_context(code, require_active=False)
    profile = build_scene_router_profile(context)
    return ApiResponse(
        data={
            "scene_code": profile.scene_code,
            "scene_name": profile.scene_name,
            "mode": profile.mode,
            "prompt": profile.prompt,
            "prompt_chars": len(profile.prompt),
            "prompt_hash": profile.prompt_hash,
            "scene_block": profile.scene_block,
            "candidate_count": len(profile.candidates),
            "candidates": [dict(c) for c in profile.candidates],
        }
    )


@router.post("/scenes/{code}/test", response_model=ApiResponse[dict])
async def router_scene_test(code: str, body: RouterTestRequest, user=Depends(require_admin)):
    """按本场景档案跑一次真实路由判定。

    - 直连场景不调用模型（返回 router_skipped=true）；
    - 其它场景会消耗一次路由模型调用；
    - 不写会话、不写记忆、不写链路，纯诊断。
    """
    message = (body.message or "").strip()
    if not message:
        raise ParamError("message 不能为空")

    context = await load_scene_context(code, require_active=False)
    profile = build_scene_router_profile(context)
    if profile.mode == RouterEffectiveMode.UNCONFIGURED.value:
        raise SceneNotConfigured()

    started = time.perf_counter()
    if profile.mode == RouterEffectiveMode.DIRECT.value:
        intents = [
            {
                "agent_name": profile.direct_agent,
                "confidence": 1.0,
                "reason": "scene_direct",
                "is_fallback": False,
            }
        ]
        result = {
            "intents": intents,
            "agent_name": profile.direct_agent,
            "confidence": 1.0,
            "reason": "scene_direct",
            "fallback_agent": profile.fallback_agent,
        }
        skipped = True
    else:
        result = await RouterAgent().route(
            message, history=body.history or None, profile=profile
        )
        skipped = False

    return ApiResponse(
        data={
            "scene_code": profile.scene_code,
            "mode": profile.mode,
            "router_skipped": skipped,
            "candidate_count": len(profile.candidates),
            "prompt_hash": profile.prompt_hash,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "agent_name": result.get("agent_name"),
            "confidence": result.get("confidence"),
            "reason": result.get("reason"),
            "intents": result.get("intents") or [],
            "fallback_agent": result.get("fallback_agent"),
        }
    )


@router.get("/scenes/{code}/stats", response_model=ApiResponse[dict])
async def router_scene_stats(code: str, hours: int = 24, user=Depends(require_admin)):
    """近 N 小时的路由质量：路由量、直连率、兜底率、转人工率与平均耗时。"""
    scene = await SceneRepo.get_by_code(code)
    if scene is None:
        raise SceneNotFound()
    data = await TraceRepo.router_stats(scene.code, hours=hours)
    data["scene_name"] = scene.display_name
    return ApiResponse(data=data)
