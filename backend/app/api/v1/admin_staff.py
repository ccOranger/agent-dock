"""后台人员与配额管理（仅管理员）。

为什么需要这个接口：
- 「登录人创建数量」的控制项本身要有地方配：``staff_users.quota`` 是 JSONB，
  配错一个键就等于没生效，所以写入必须白名单校验（quota_service.normalize_quota）；
- 已用数量必须能看到，否则运营只知道"创建失败"，不知道差多少。

保护规则（防自锁）：
- 不能停用或降级自己；
- 不能改自己的 quota；
- 不提供物理删除，只停用——历史资源仍挂着 created_by，删人会留下悬空引用。
"""
import logging
import uuid

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.core.exceptions import Conflict, NotFound, ParamError
from app.core.security import hash_password
from app.models.repositories import AuditRepo, RoleRepo, StaffRepo
from app.schemas.api import StaffCreate, StaffPasswordReset, StaffUpdate
from app.schemas.common import ApiResponse
from app.services.quota_service import (
    LIMIT_KEYS,
    USAGE_KEYS,
    effective_limits,
    normalize_quota,
    staff_usage,
    usage_by_owner,
)

router = APIRouter(prefix="/admin/staff", tags=["admin-staff"])

logger = logging.getLogger(__name__)


def _uuid_or_400(value: str, field: str = "staff_id") -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ParamError(f"{field} 不是合法 UUID: {value}") from exc


def _to_dict(user, usage: dict[str, int], roles: list[str] | None = None) -> dict:
    """一行人员 + 角色 + 配额 + 已用 + 剩余（remaining=null 表示不限）。"""
    limits = effective_limits(user)
    quota = {LIMIT_KEYS[kind]: limits[kind] for kind in LIMIT_KEYS if limits[kind] is not None}
    used = {USAGE_KEYS[kind]: int(usage.get(kind, 0)) for kind in LIMIT_KEYS}
    remaining = {
        USAGE_KEYS[kind]: (
            None if limits[kind] is None else max(limits[kind] - used[USAGE_KEYS[kind]], 0)
        )
        for kind in LIMIT_KEYS
    }
    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "roles": roles or [user.role],
        "display_name": user.display_name,
        "is_active": bool(user.is_active),
        "quota": quota,
        "used": used,
        "remaining": remaining,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("", response_model=ApiResponse[list])
async def list_staff(user=Depends(require_admin)):
    """全部人员 + 配额 + 已用数量。已用数量按 created_by 分组一次取回，禁止逐人 count。"""
    users = await StaffRepo.list_all()
    usage = {kind: await usage_by_owner(kind) for kind in LIMIT_KEYS}
    data = []
    for row in users:
        per_kind = {kind: usage[kind].get(str(row.id), 0) for kind in LIMIT_KEYS}
        roles = await RoleRepo.get_user_role_keys(row.id) or [row.role]
        data.append(_to_dict(row, per_kind, roles=roles))
    return ApiResponse(data=data)


@router.post("", response_model=ApiResponse[dict])
async def create_staff(body: StaffCreate, user=Depends(require_admin)):
    if await StaffRepo.get_by_username(body.username) is not None:
        raise Conflict(f"用户名已存在: {body.username}")
    quota = normalize_quota(body.quota)
    # 主角色列与历史 require_staff/require_admin 兼容：多角色下取集合里是否含 admin
    primary = "admin" if (body.roles and "admin" in body.roles) else body.role
    row = await StaffRepo.create(
        username=body.username,
        password_hash=hash_password(body.password),
        role=primary,
        display_name=body.display_name,
        quota=quota,
    )
    role_keys = await RoleRepo.assign_user_roles(row.id, body.roles or [primary])
    await AuditRepo.write(
        action="admin.staff.create", user_id=str(user.id), target_type="staff", target_id=str(row.id)
    )
    return ApiResponse(data=_to_dict(row, {}, roles=role_keys))


@router.patch("/{staff_id}", response_model=ApiResponse[dict])
async def update_staff(staff_id: str, body: StaffUpdate, user=Depends(require_admin)):
    target_id = _uuid_or_400(staff_id)
    row = await StaffRepo.get_by_id(target_id)
    if row is None:
        raise NotFound("人员不存在")

    is_self = target_id == str(user.id)
    fields: dict = {}
    if "display_name" in body.model_fields_set:
        # 显式传 null / 空串 = 清空展示名：不能用 is not None 判定，否则永远清不掉
        fields["display_name"] = (body.display_name or "").strip() or None
    if body.roles is not None:
        # 角色集合整体替换：不允许多人自改自锁（与历史“不能改自己角色”一致）
        if is_self:
            raise ParamError("不能修改自己的角色集合")
        applied = await RoleRepo.assign_user_roles(target_id, body.roles)
        fields["role"] = "admin" if "admin" in applied else "agent"
    elif body.role is not None:
        if is_self and body.role != row.role:
            raise ParamError("不能修改自己的角色")
        fields["role"] = body.role
        # 单角色入参：保持 user_roles 与主角色列同步
        await RoleRepo.assign_user_roles(target_id, [body.role])
    if body.is_active is not None:
        if is_self and not body.is_active:
            raise ParamError("不能停用自己的账号")
        fields["is_active"] = body.is_active
    if body.quota is not None:
        if is_self:
            raise ParamError("不能修改自己的配额，请由其他管理员操作")
        fields["quota"] = normalize_quota(body.quota)

    updated = await StaffRepo.update(target_id, **fields) if fields else row
    roles_out = await RoleRepo.get_user_role_keys(target_id) or [updated.role]
    await AuditRepo.write(
        action="admin.staff.update",
        user_id=str(user.id),
        target_type="staff",
        target_id=target_id,
        payload=fields,
    )
    return ApiResponse(data=_to_dict(updated, await staff_usage(target_id), roles=roles_out))


@router.post("/{staff_id}/password", response_model=ApiResponse)
async def reset_staff_password(
    staff_id: str, body: StaffPasswordReset, user=Depends(require_admin)
):
    target_id = _uuid_or_400(staff_id)
    ok = await StaffRepo.set_password(target_id, hash_password(body.password))
    if not ok:
        raise NotFound("人员不存在")
    await AuditRepo.write(
        action="admin.staff.reset_password",
        user_id=str(user.id),
        target_type="staff",
        target_id=target_id,
    )
    return ApiResponse(data={"reset": True})
