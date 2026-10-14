"""API 依赖：认证、RBAC（人员 / 客户分表后按 role 路由到对应仓储）"""
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import Forbidden, TokenExpired, Unauthorized
from app.core.security import decode_token
from app.models.repositories import CustomerRepo, StaffRepo

bearer_scheme = HTTPBearer(auto_error=False)

STAFF_ROLES = ("agent", "admin")


async def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """解析 Bearer Token，按 role 声明到对应表查询并返回账号对象。

    - role == customer → customer_users 表（CustomerUser）
    - role in (agent, admin) → staff_users 表（StaffUser）
    """
    if creds is None:
        raise Unauthorized()
    payload = decode_token(creds.credentials)
    if payload is None:
        raise TokenExpired()
    role = payload.get("role")
    sub = payload.get("sub")
    if not sub:
        raise Unauthorized()
    if role == "customer":
        user = await CustomerRepo.get_by_id(sub)
    elif role in STAFF_ROLES:
        user = await StaffRepo.get_by_id(sub)
    else:
        raise Unauthorized()
    if user is None or not user.is_active:
        raise Unauthorized()
    request.state.user_id = str(user.id)
    return user


async def current_customer(user=Depends(current_user)):
    """限定客户身份（聊天 / 会话 / 我的记忆等客户侧接口）。"""
    if user.role != "customer":
        raise Forbidden()
    return user


async def current_staff(user=Depends(current_user)):
    """限定后台人员身份（agent / admin）。"""
    if user.role not in STAFF_ROLES:
        raise Forbidden()
    return user


def require_roles(*roles: str):
    """RBAC 依赖工厂：限定角色"""

    async def _dep(user=Depends(current_user)):
        if user.role not in roles:
            raise Forbidden()
        return user

    return _dep


require_staff = require_roles("agent", "admin")
require_admin = require_roles("admin")
