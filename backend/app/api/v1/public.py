"""公开接入接口（无需登录）：嵌入场景配置与访客签发。

这里只暴露「宿主页面渲染需要知道的东西」（展示配置、是否允许匿名、可用的
鉴权方式）。Agent 清单、路由提示词、知识库、兜底话术一律不出现在响应里，
避免嵌入代码把平台内部编排泄露给第三方页面。
"""
import secrets

from fastapi import APIRouter, Request

from app.core.exceptions import Forbidden, SceneDisabled, SceneNotFound, Unauthorized
from app.models.repositories import SceneRepo, VisitorRepo
from app.schemas.common import ApiResponse
from app.schemas.scene import SceneStatus
from app.services.widget_config import public_widget_config

router = APIRouter(prefix="/public/scenes", tags=["public"])


async def _active_scene(code: str):
    """公开接口同样遵守「错码 3003 / 停用 3004」，不静默回落默认场景。"""
    scene = await SceneRepo.get_by_code(code)
    if scene is None:
        raise SceneNotFound()
    if scene.status != SceneStatus.ACTIVE.value:
        raise SceneDisabled()
    return scene


@router.get("/{code}", response_model=ApiResponse[dict])
async def get_public_scene(code: str):
    """嵌入组件初始化配置：只读展示信息，不含任何编排 / 知识内容。"""
    scene = await _active_scene(code)
    published = bool(scene.publish_key)
    auth_modes = ["customer_token", "api_key"]
    if published:
        auth_modes.insert(0, "anonymous")
    return ApiResponse(
        data={
            "code": scene.code,
            "display_name": scene.display_name,
            "description": scene.description,
            "allow_anonymous": published,
            # publish_key 是公开标识（会原样出现在宿主页面源码里），不是密钥；
            # 真正的服务端接入凭证只返回是否已配置。
            "publish_key": scene.publish_key,
            "auth_modes": auth_modes,
            "widget": public_widget_config(scene),
        }
    )


@router.post("/{code}/visitors", response_model=ApiResponse[dict])
async def issue_visitor(code: str, request: Request):
    """为已发布场景签发匿名访客标识。

    返回的 visitor_id 是宿主页面保存到 localStorage 的稳定标识（对应
    visitors.external_key），后续聊天原样传入即可复用同一访客的会话与记忆。
    场景未发布时拒绝签发：避免未接入的场景被批量刷访客行。
    """
    scene = await _active_scene(code)
    if not scene.publish_key:
        raise Forbidden("该场景未开放匿名接入")
    given = request.headers.get("x-publish-key")
    if given and given != scene.publish_key:
        raise Unauthorized("发布标识无效")

    visitor_key = f"v_{secrets.token_urlsafe(24)}"
    await VisitorRepo.get_or_create(scene.id, external_key=visitor_key)
    return ApiResponse(
        data={"visitor_id": visitor_key, "scene_code": scene.code}
    )
