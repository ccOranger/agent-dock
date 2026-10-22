"""后台接入方管理：服务端 Key 归属、场景授权与 Key 签发/吊销。

Key 挂在接入方而不是场景上：场景是业务维度，接入方是调用方维度。
一个接入方可持有多把 Key（轮换过渡期新旧并存），停用接入方会立即让全部 Key 失效。
"""
import secrets
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError

from app.api.deps import require_admin
from app.core.exceptions import Conflict, NotFound, ParamError
from app.models.repositories import AuditRepo, IntegrationRepo, SceneRepo
from app.schemas.common import ApiResponse
from app.schemas.integration import IntegrationCreate, IntegrationUpdate

router = APIRouter(prefix="/admin/integrations", tags=["admin-integrations"])


def _uuid_or_400(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ParamError(f"{field} 不是合法 UUID: {value}") from exc


def _normalize_scene_ids(values: list[str]) -> list[str]:
    """归一化 UUID 并保序去重。"""
    return list(dict.fromkeys(str(_uuid_or_400(v, "scene_ids")) for v in values))


async def _validate_scene_ids(values: list[str]) -> list[str]:
    ids = _normalize_scene_ids(values)
    existing = await SceneRepo.existing_ids([uuid.UUID(x) for x in ids])
    invalid = [x for x in ids if x not in existing]
    if invalid:
        raise ParamError(f"以下场景不存在: {invalid}")
    return ids


async def _get_or_404(integration_id: str):
    _uuid_or_400(integration_id, "integration_id")
    row = await IntegrationRepo.get(integration_id)
    if row is None:
        raise NotFound("接入方不存在")
    return row


@router.get("", response_model=ApiResponse[list])
async def list_integrations(user=Depends(require_admin)):
    return ApiResponse(data=await IntegrationRepo.list_all())


@router.post("", response_model=ApiResponse[dict])
async def create_integration(body: IntegrationCreate, user=Depends(require_admin)):
    name = body.name.strip()
    if not name:
        raise ParamError("接入方名称不能为空")
    scene_ids = await _validate_scene_ids(body.scene_ids)
    try:
        row = await IntegrationRepo.create(
            name=name, description=body.description, scene_ids=scene_ids
        )
    except IntegrityError as exc:
        raise Conflict(f"接入方名称已存在: {name}") from exc
    await AuditRepo.write(
        "integration.create",
        user_id=user.id,
        target_type="integration",
        target_id=str(row.id),
    )
    return ApiResponse(data=await IntegrationRepo.detail(row.id))


@router.get("/{integration_id}", response_model=ApiResponse[dict])
async def get_integration(integration_id: str, user=Depends(require_admin)):
    row = await _get_or_404(integration_id)
    data = await IntegrationRepo.detail(row.id)
    return ApiResponse(data=data)


@router.put("/{integration_id}", response_model=ApiResponse[dict])
async def update_integration(
    integration_id: str, body: IntegrationUpdate, user=Depends(require_admin)
):
    row = await _get_or_404(integration_id)
    fields = body.model_dump(exclude_unset=True)
    scene_ids = fields.pop("scene_ids", None)
    if "name" in fields:
        fields["name"] = (fields["name"] or "").strip()
        if not fields["name"]:
            raise ParamError("接入方名称不能为空")
    if scene_ids is not None:
        scene_ids = await _validate_scene_ids(scene_ids)
    try:
        await IntegrationRepo.update(row.id, scene_ids=scene_ids, **fields)
    except IntegrityError as exc:
        raise Conflict(f"接入方名称已存在: {fields.get('name')}") from exc
    await AuditRepo.write(
        "integration.update",
        user_id=user.id,
        target_type="integration",
        target_id=str(row.id),
    )
    return ApiResponse(data=await IntegrationRepo.detail(row.id))


@router.delete("/{integration_id}", response_model=ApiResponse[dict])
async def delete_integration(integration_id: str, user=Depends(require_admin)):
    row = await _get_or_404(integration_id)
    if await IntegrationRepo.has_visitors(row.id):
        raise Conflict("该接入方已有访客会话，请改为停用，避免删除时连带清空会话")
    await IntegrationRepo.delete(row.id)
    await AuditRepo.write(
        "integration.delete",
        user_id=user.id,
        target_type="integration",
        target_id=str(row.id),
    )
    return ApiResponse(data={"deleted": True})


@router.post("/{integration_id}/keys", response_model=ApiResponse[dict])
async def issue_integration_key(integration_id: str, user=Depends(require_admin)):
    """签发服务端 Key：明文只在本次响应出现一次，库里只存 sha256。"""
    row = await _get_or_404(integration_id)
    raw = f"sk_{secrets.token_urlsafe(32)}"
    # 尾号便于后台区分多把 Key；6 位不足以还原明文
    key = await IntegrationRepo.create_key(row.id, raw=raw, hint=raw[-6:])
    await AuditRepo.write(
        "integration.key.create",
        user_id=user.id,
        target_type="integration",
        target_id=str(row.id),
    )
    return ApiResponse(data={"id": str(key.id), "api_key": raw, "hint": key.hint})


@router.delete("/{integration_id}/keys/{key_id}", response_model=ApiResponse[dict])
async def revoke_integration_key(
    integration_id: str, key_id: str, user=Depends(require_admin)
):
    row = await _get_or_404(integration_id)
    _uuid_or_400(key_id, "key_id")
    if not await IntegrationRepo.revoke_key(row.id, key_id):
        raise NotFound("Key 不存在或不属于该接入方")
    await AuditRepo.write(
        "integration.key.revoke",
        user_id=user.id,
        target_type="integration",
        target_id=str(row.id),
    )
    return ApiResponse(data={"revoked": True})
