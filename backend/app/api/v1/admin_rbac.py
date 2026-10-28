"""角色与菜单权限管理（RBAC，仅管理员）。

- 菜单模块清单来自 ``app.core.rbac``（与前端菜单叶子、后端 require_perm 对齐的单一事实源）；
- 角色：内置 admin/agent 不可删、key 不可改；自定义角色可增删改；
- 授权：按角色整体替换 ``{menu_key: 'read'|'write'}`` 矩阵，非法 key/等级在接口层过滤。
"""
from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.params import parse_uuid
from app.core.exceptions import Conflict, NotFound, ParamError
from app.core.rbac import ACCESS_LEVELS, MENU_KEYS, MENU_MODULES
from app.models.repositories import AuditRepo, RoleRepo
from app.schemas.api import PermissionGrant, RoleCreate, RoleOut, RoleUpdate
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/admin/rbac", tags=["admin-rbac"])


def _clean_permissions(permissions: dict[str, str]) -> dict[str, str]:
    """只保留合法菜单 key 与合法 access 等级；越权的写入在这里被丢弃。"""
    return {
        key: level
        for key, level in permissions.items()
        if key in MENU_KEYS and level in ACCESS_LEVELS
    }


@router.get("/menus", response_model=ApiResponse[list])
async def list_menu_modules(user=Depends(require_admin)):
    """可授权菜单模块清单（角色配置页渲染读写矩阵的数据源）。"""
    return ApiResponse(data=MENU_MODULES)


@router.get("/roles", response_model=ApiResponse[list])
async def list_roles(user=Depends(require_admin)):
    """全部角色 + 授权矩阵 + 在册人数。"""
    roles = await RoleRepo.list_roles()
    counts = await RoleRepo.user_counts()
    data: list[dict] = []
    for role in roles:
        perms = await RoleRepo.get_permissions(role.id)
        data.append(
            RoleOut(
                id=str(role.id),
                key=role.key,
                name=role.name,
                description=role.description,
                is_system=role.is_system,
                permissions=perms,
                user_count=counts.get(str(role.id), 0),
            ).model_dump()
        )
    return ApiResponse(data=data)


@router.post("/roles", response_model=ApiResponse[dict])
async def create_role(body: RoleCreate, user=Depends(require_admin)):
    if await RoleRepo.get_role_by_key(body.key) is not None:
        raise Conflict(f"角色标识已存在: {body.key}")
    role = await RoleRepo.create_role(
        key=body.key, name=body.name, description=body.description
    )
    permissions = _clean_permissions(body.permissions or {})
    if permissions:
        await RoleRepo.set_permissions(role.id, permissions)
    await AuditRepo.write(
        action="admin.rbac.role_create", user_id=str(user.id),
        target_type="role", target_id=str(role.id), payload={"key": body.key},
    )
    return ApiResponse(data=RoleOut(
        id=str(role.id), key=role.key, name=role.name, description=role.description,
        is_system=role.is_system, permissions=permissions, user_count=0,
    ).model_dump())


@router.patch("/roles/{role_id}", response_model=ApiResponse[dict])
async def update_role(role_id: str, body: RoleUpdate, user=Depends(require_admin)):
    rid = parse_uuid(role_id)
    role = await RoleRepo.get_role(rid)
    if role is None:
        raise NotFound("角色不存在")
    updated = await RoleRepo.update_role(
        rid, name=body.name, description=body.description
    )
    perms = await RoleRepo.get_permissions(rid)
    counts = await RoleRepo.user_counts()
    return ApiResponse(data=RoleOut(
        id=str(updated.id), key=updated.key, name=updated.name,
        description=updated.description, is_system=updated.is_system,
        permissions=perms, user_count=counts.get(str(rid), 0),
    ).model_dump())


@router.delete("/roles/{role_id}", response_model=ApiResponse)
async def delete_role(role_id: str, user=Depends(require_admin)):
    rid = parse_uuid(role_id)
    role = await RoleRepo.get_role(rid)
    if role is None:
        raise NotFound("角色不存在")
    if role.is_system:
        raise ParamError("内置角色不可删除")
    await RoleRepo.delete_role(rid)
    await AuditRepo.write(
        action="admin.rbac.role_delete", user_id=str(user.id),
        target_type="role", target_id=str(rid),
    )
    return ApiResponse(data={"deleted": True})


@router.get("/roles/{role_id}/permissions", response_model=ApiResponse[dict])
async def get_role_permissions(role_id: str, user=Depends(require_admin)):
    rid = parse_uuid(role_id)
    role = await RoleRepo.get_role(rid)
    if role is None:
        raise NotFound("角色不存在")
    return ApiResponse(data=await RoleRepo.get_permissions(rid))


@router.put("/roles/{role_id}/permissions", response_model=ApiResponse[dict])
async def set_role_permissions(
    role_id: str, body: PermissionGrant, user=Depends(require_admin)
):
    rid = parse_uuid(role_id)
    role = await RoleRepo.get_role(rid)
    if role is None:
        raise NotFound("角色不存在")
    permissions = _clean_permissions(body.permissions)
    await RoleRepo.set_permissions(rid, permissions)
    await AuditRepo.write(
        action="admin.rbac.role_grant", user_id=str(user.id),
        target_type="role", target_id=str(rid), payload={"count": len(permissions)},
    )
    return ApiResponse(data=permissions)
