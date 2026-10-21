"""后台场景管理：CRUD 与 Agent 绑定。

默认场景是平台的兜底落点，只允许改展示名与描述：
- 不能改 code / 路由模式 / 绑定 Agent，避免把「无 code 请求」打到一个业务 Agent 上；
- 不能停用、不能删除。
"""
import secrets
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError

from app.api.deps import require_admin, require_staff
from app.core.cors import invalidate_origin_cache
from app.core.exceptions import Conflict, NotFound, ParamError
from app.models.repositories import AgentRepo, AuditRepo, KnowledgeBaseRepo, SceneRepo
from app.schemas.common import ApiResponse
from app.schemas.scene import DEFAULT_SCENE_CODE, SceneStatus
from app.schemas.scene_admin import (
    SceneAgentsUpdate,
    SceneCreate,
    SceneKnowledgeBasesUpdate,
    SceneUpdate,
    SceneWidgetUpdate,
)
from app.services.scene_service import hash_api_key
from app.services.widget_config import normalize_origins, normalize_widget_config

router = APIRouter(prefix="/admin/scenes", tags=["admin-scenes"])

_DEFAULT_READONLY_HINT = "默认场景只允许修改展示名与描述"


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


async def _validate_bound_agents(scene_id, agent_ids: list[str]) -> list[str]:
    """绑定白名单：只允许全局 Agent 或本场景专属 Agent。"""
    allowed = {item["id"] for item in await SceneRepo.list_agents_for_admin(scene_id)}
    invalid = [agent_id for agent_id in agent_ids if agent_id not in allowed]
    if invalid:
        raise ParamError(f"以下 Agent 不可绑定到本场景: {invalid}")
    return agent_ids


@router.get("", response_model=ApiResponse[list])
async def list_scenes(user=Depends(require_admin)):
    return ApiResponse(data=await SceneRepo.list_with_agent_counts())


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
    scene = await SceneRepo.create(
        code=body.code,
        display_name=body.display_name,
        description=body.description,
        router_mode=body.router_mode,
        fallback_agent_id=fallback_id,
        fallback_text=body.fallback_text,
        extra_router_prompt=body.extra_router_prompt,
    )
    if agent_ids:
        await SceneRepo.set_agents(scene.id, agent_ids)
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
    return ApiResponse(data=data)


@router.patch("/{scene_id}", response_model=ApiResponse[dict])
async def update_scene(scene_id: str, body: SceneUpdate, user=Depends(require_admin)):
    scene = await _get_scene_or_404(scene_id)
    fields = body.model_dump(exclude_unset=True)
    agent_ids = fields.pop("agent_ids", None)

    if scene.is_default:
        blocked = set(fields) - {"display_name", "description"}
        if blocked or agent_ids is not None:
            raise ParamError(_DEFAULT_READONLY_HINT)
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


async def _knowledge_base_options(scene_id) -> list[dict]:
    """可绑定知识库 + 当前归属：其他场景占用的库不允许抢绑。"""
    scene_codes = {str(s.id): s.code for s in await SceneRepo.list_all()}
    data = []
    for row in await KnowledgeBaseRepo.list_all():
        owner = str(row.scene_id) if row.scene_id else None
        data.append(
            {
                "namespace": row.namespace,
                "display_name": row.display_name,
                "is_active": row.is_active,
                "scene_id": owner,
                "scene_code": scene_codes.get(owner) if owner else None,
                "bound": owner == str(scene_id),
                "available": owner is None or owner == str(scene_id),
            }
        )
    return data


@router.get("/{scene_id}/knowledge-bases", response_model=ApiResponse[list])
async def list_scene_knowledge_base_options(scene_id: str, user=Depends(require_admin)):
    """知识库绑定弹窗：全部库 + 归属状态（被其他场景占用时不可选）。"""
    scene = await _get_scene_or_404(scene_id)
    return ApiResponse(data=await _knowledge_base_options(scene.id))


@router.put("/{scene_id}/knowledge-bases", response_model=ApiResponse[list])
async def set_scene_knowledge_bases(
    scene_id: str, body: SceneKnowledgeBasesUpdate, user=Depends(require_admin)
):
    """全量替换知识库归属；默认场景只读（避免改变无码请求的检索范围）。"""
    scene = await _get_scene_or_404(scene_id)
    if scene.is_default:
        raise ParamError(_DEFAULT_READONLY_HINT)
    namespaces = list(dict.fromkeys(body.namespaces))
    rows = {r.namespace: r for r in await KnowledgeBaseRepo.list_all()}
    unknown = [n for n in namespaces if n not in rows]
    if unknown:
        raise ParamError(f"以下知识库不存在: {unknown}")
    stolen = [
        n
        for n in namespaces
        if rows[n].scene_id is not None and str(rows[n].scene_id) != str(scene.id)
    ]
    if stolen:
        raise ParamError(f"以下知识库已被其他场景占用: {stolen}")
    await KnowledgeBaseRepo.set_scene_namespaces(scene.id, namespaces)
    await AuditRepo.write(
        "scene.set_knowledge_bases",
        user_id=user.id,
        target_type="scene",
        target_id=str(scene.id),
    )
    return ApiResponse(data=await _knowledge_base_options(scene.id))


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


@router.post("/{scene_id}/rotate-key", response_model=ApiResponse[dict])
async def rotate_scene_api_key(scene_id: str, user=Depends(require_admin)):
    """轮换服务端接入 key：明文只在本次响应出现一次，库里只存 sha256。"""
    scene = await _get_scene_or_404(scene_id)
    raw = f"sk_{secrets.token_urlsafe(32)}"
    await SceneRepo.update(scene.id, api_key_hash=hash_api_key(raw))
    await AuditRepo.write(
        "scene.rotate_key",
        user_id=user.id,
        target_type="scene",
        target_id=str(scene.id),
    )
    return ApiResponse(data={"api_key": raw, "api_key_set": True})


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
