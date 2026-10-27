"""后台场景管理：CRUD 与 Agent 绑定。

默认场景是平台的兜底落点，只允许改展示名与描述：
- 不能改 code / 路由模式 / 绑定 Agent，避免把「无 code 请求」打到一个业务 Agent 上；
- 不能停用、不能删除。
"""
import logging
import secrets
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError

from app.api.deps import require_admin, require_staff
from app.core.cors import invalidate_origin_cache
from app.core.exceptions import Conflict, NotFound, ParamError
from app.models.repositories import (
    AgentRepo,
    AuditRepo,
    ChatEventRepo,
    IntegrationRepo,
    SceneRepo,
)
from app.schemas.common import ApiResponse
from app.schemas.scene import DEFAULT_SCENE_CODE, SceneStatus
from app.schemas.scene_admin import (
    SceneAgentsUpdate,
    SceneCreate,
    SceneUpdate,
    SceneWidgetUpdate,
)
from app.services.canned_templates import ensure_scene_templates_committed
from app.services.quota_service import KIND_SCENE, create_guard
from app.services.runtime_config import (
    normalize_runtime_config,
    resolve_runtime,
    runtime_effective_dict,
    validate_runtime_namespaces,
)
from app.services.widget_config import normalize_origins, normalize_widget_config

router = APIRouter(prefix="/admin/scenes", tags=["admin-scenes"])

logger = logging.getLogger(__name__)

_DEFAULT_READONLY_HINT = "默认场景只允许修改展示名、描述与运行参数（检索/历史/额度）"
# 默认场景可变字段：运行参数不影响路由与绑定（路由仍固定直连 default_assistant）
_DEFAULT_EDITABLE = {"display_name", "description", "runtime_config"}


def _uuid_or_400(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ParamError(f"{field} 不是合法 UUID: {value}") from exc


def _normalize_ids(values: list[str]) -> list[str]:
    """归一化 UUID 字符串并保序去重。"""
    return list(dict.fromkeys(str(_uuid_or_400(v, "agent_ids")) for v in values))


async def _get_scene_or_404(scene_id: str):
    _uuid_or_400(scene_id, "scene_id")
    scene = await SceneRepo.get(scene_id)
    if scene is None:
        raise NotFound("场景不存在")
    return scene


async def _normalize_runtime(payload: dict | None) -> dict:
    """校验并归一化 runtime_config；知识库引用必须是存在且启用中的库。"""
    config = normalize_runtime_config(payload)
    await validate_runtime_namespaces(config)
    return config


async def _validate_bound_agents(scene_id, agent_ids: list[str]) -> list[str]:
    """绑定白名单：只允许全局 Agent 或本场景专属 Agent。"""
    allowed = {item["id"] for item in await SceneRepo.list_agents_for_admin(scene_id)}
    invalid = [agent_id for agent_id in agent_ids if agent_id not in allowed]
    if invalid:
        raise ParamError(f"以下 Agent 不可绑定到本场景: {invalid}")
    return agent_ids


@router.get("", response_model=ApiResponse[list])
async def list_scenes(user=Depends(require_admin)):
    """场景列表 + 今日请求量与日额度（一次聚合，不逐场景查 chat_events）。"""
    rows = await SceneRepo.list_with_agent_counts()
    today = await ChatEventRepo.count_by_scene_today()
    for row in rows:
        runtime = resolve_runtime(row.get("runtime_config"))
        row["today_requests"] = today.get(row["code"], 0)
        row["quota_daily"] = runtime.quota.daily
        row["quota_source"] = runtime.sources.get("quota.daily", "default")
    return ApiResponse(data=rows)


@router.get("/options", response_model=ApiResponse[list])
async def scene_options(user=Depends(require_staff)):
    """聊天入口/前端下拉用：只返回启用中的场景（含 id，便于资源归属回显）。"""
    rows = [s for s in await SceneRepo.list_all() if s.status == SceneStatus.ACTIVE.value]
    return ApiResponse(
        data=[
            {
                "id": str(s.id),
                "code": s.code,
                "display_name": s.display_name,
                "is_default": bool(s.is_default),
            }
            for s in rows
        ]
    )


@router.post("", response_model=ApiResponse[dict])
async def create_scene(body: SceneCreate, user=Depends(require_admin)):
    if body.code == DEFAULT_SCENE_CODE:
        raise Conflict("default 是保留场景标识")
    if await SceneRepo.get_by_code(body.code) is not None:
        raise Conflict(f"场景标识已存在: {body.code}")

    # 新建场景只能绑全局 Agent：场景专属 Agent 必须先建在该场景下
    if body.agent_ids:
        agent_ids = _normalize_ids(body.agent_ids)
        global_ids = {str(a.id) for a in await AgentRepo.list_all() if a.scene_id is None}
        invalid = [x for x in agent_ids if x not in global_ids]
        if invalid:
            raise ParamError(f"新场景只能绑定全局 Agent，非法项: {invalid}")
    else:
        agent_ids = []

    fallback_id = (
        str(_uuid_or_400(body.fallback_agent_id, "fallback_agent_id"))
        if body.fallback_agent_id
        else None
    )
    runtime_config = await _normalize_runtime(body.runtime_config)
    async with create_guard(user, KIND_SCENE):
        scene = await SceneRepo.create(
            code=body.code,
            display_name=body.display_name,
            description=body.description,
            router_mode=body.router_mode,
            fallback_agent_id=fallback_id,
            fallback_text=body.fallback_text,
            extra_router_prompt=body.extra_router_prompt,
            runtime_config=runtime_config,
            created_by=user.id,
        )
    if agent_ids:
        await SceneRepo.set_agents(scene.id, agent_ids)
    # 单租户降级：新场景自动授权给全部接入方，避免已有 Key 调用新场景 403
    try:
        await IntegrationRepo.grant_scene_to_all(scene.id)
    except Exception as exc:  # noqa: BLE001 - 授权失败不回滚已创建的场景
        logger.warning(f"新场景自动授权接入方失败（不影响场景创建）: {exc}")
    # 新场景自动获得自己的身份/能力话术：否则客户问“你是谁”永远回平台统一文案
    try:
        await ensure_scene_templates_committed(scene.id)
    except Exception as exc:  # noqa: BLE001 - 话术补齐失败不回滚已创建的场景
        logger.warning(f"新场景初始化固定话术失败（不影响场景创建）: {exc}")
    await AuditRepo.write(
        "scene.create", user_id=user.id, target_type="scene", target_id=str(scene.id)
    )
    return ApiResponse(data=SceneRepo.to_dict(scene, len(agent_ids)))


@router.get("/{scene_id}", response_model=ApiResponse[dict])
async def get_scene(scene_id: str, user=Depends(require_admin)):
    scene = await _get_scene_or_404(scene_id)
    agents = await SceneRepo.list_bound_agents(scene.id)
    data = SceneRepo.to_dict(scene, len(agents))
    data["bound_agent_ids"] = await SceneRepo.get_bound_agent_ids(scene.id)
    # 后台展示“生效值 + 每个键的来源”，与运行时 resolve_runtime 同源
    runtime = resolve_runtime(data["runtime_config"])
    data["runtime_effective"] = runtime_effective_dict(runtime)
    data["runtime_sources"] = dict(runtime.sources)
    return ApiResponse(data=data)


@router.patch("/{scene_id}", response_model=ApiResponse[dict])
async def update_scene(scene_id: str, body: SceneUpdate, user=Depends(require_admin)):
    scene = await _get_scene_or_404(scene_id)
    fields = body.model_dump(exclude_unset=True)
    agent_ids = fields.pop("agent_ids", None)

    if scene.is_default:
        blocked = set(fields) - _DEFAULT_EDITABLE
        if blocked or agent_ids is not None:
            raise ParamError(_DEFAULT_READONLY_HINT)
    if "runtime_config" in fields:
        # 整对象替换：null 等于清空覆盖项（列是 NOT NULL，这里转成空对象）
        fields["runtime_config"] = await _normalize_runtime(fields["runtime_config"])
    if "fallback_agent_id" in fields:
        raw = fields["fallback_agent_id"]
        fields["fallback_agent_id"] = (
            str(_uuid_or_400(raw, "fallback_agent_id")) if raw else None
        )
    if agent_ids is not None:
        agent_ids = _normalize_ids(agent_ids)
        await _validate_bound_agents(scene.id, agent_ids)

    if agent_ids is not None:
        await SceneRepo.set_agents(scene.id, agent_ids)
    if fields:
        scene = await SceneRepo.update(scene.id, **fields) or scene
    await AuditRepo.write(
        "scene.update", user_id=user.id, target_type="scene", target_id=str(scene.id)
    )
    agents = await SceneRepo.list_bound_agents(scene.id)
    data = SceneRepo.to_dict(scene, len(agents))
    data["bound_agent_ids"] = await SceneRepo.get_bound_agent_ids(scene.id)
    return ApiResponse(data=data)


@router.put("/{scene_id}/agents", response_model=ApiResponse[list])
async def set_scene_agents(
    scene_id: str, body: SceneAgentsUpdate, user=Depends(require_admin)
):
    scene = await _get_scene_or_404(scene_id)
    if scene.is_default:
        raise ParamError(_DEFAULT_READONLY_HINT)
    agent_ids = _normalize_ids(body.agent_ids)
    await _validate_bound_agents(scene.id, agent_ids)
    await SceneRepo.set_agents(scene.id, agent_ids)
    await AuditRepo.write(
        "scene.set_agents", user_id=user.id, target_type="scene", target_id=str(scene.id)
    )
    return ApiResponse(data=await SceneRepo.list_bound_agents(scene.id))


@router.get("/{scene_id}/agents", response_model=ApiResponse[list])
async def list_scene_agent_options(scene_id: str, user=Depends(require_admin)):
    """绑定弹窗：可绑定 Agent + 当前绑定状态。"""
    scene = await _get_scene_or_404(scene_id)
    return ApiResponse(data=await SceneRepo.list_agents_for_admin(scene.id))


@router.put("/{scene_id}/widget", response_model=ApiResponse[dict])
async def update_scene_widget(
    scene_id: str, body: SceneWidgetUpdate, user=Depends(require_admin)
):
    """保存嵌入配置、允许来源与发布开关。

    默认场景也允许配置嵌入（它是最常被第三方引用的通用客服入口），
    这里只动展示与接入配置，不影响路由/Agent 绑定。
    """
    scene = await _get_scene_or_404(scene_id)
    fields: dict = {}
    if body.widget_config is not None:
        fields["widget_config"] = normalize_widget_config(body.widget_config)
    if body.allowed_origins is not None:
        try:
            fields["allowed_origins"] = normalize_origins(body.allowed_origins)
        except ValueError as exc:
            raise ParamError(str(exc)) from exc
    if body.allow_anonymous is not None:
        if body.allow_anonymous:
            # 已有发布标识保持不变：关闭再打开不应悄悄换掉已嵌入页面用的 key
            if not scene.publish_key:
                fields["publish_key"] = f"pk_{secrets.token_urlsafe(18)}"
        else:
            fields["publish_key"] = None

    if fields:
        scene = await SceneRepo.update(scene.id, **fields) or scene
    # 来源配置变了，进程内缓存必须立刻失效，否则新来源要等 TTL 才生效
    invalidate_origin_cache(scene.code)
    await AuditRepo.write(
        "scene.update_widget",
        user_id=user.id,
        target_type="scene",
        target_id=str(scene.id),
    )
    return ApiResponse(data=SceneRepo.to_dict(scene))


@router.delete("/{scene_id}", response_model=ApiResponse[dict])
async def delete_scene(scene_id: str, user=Depends(require_admin)):
    scene = await _get_scene_or_404(scene_id)
    if scene.is_default:
        raise ParamError("默认场景不允许删除")
    try:
        await SceneRepo.delete(scene.id)
    except IntegrityError as exc:
        raise Conflict("场景仍有关联的会话/知识库/话术，请改为停用或归档") from exc
    await AuditRepo.write(
        "scene.delete", user_id=user.id, target_type="scene", target_id=str(scene.id)
    )
    return ApiResponse(data={"deleted": True})
