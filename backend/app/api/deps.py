"""API 依赖：认证、RBAC"""
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import Forbidden, TokenExpired, Unauthorized
from app.core.security import decode_token
from app.models.repositories import UserRepo

bearer_scheme = HTTPBearer(auto_error=False)


async def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """解析 Bearer Token，返回当前用户 ORM 对象"""
    if creds is None:
        raise Unauthorized()
    payload = decode_token(creds.credentials)
    if payload is None:
        raise TokenExpired()
    user = await UserRepo.get_by_id(payload["sub"])
    if user is None or not user.is_active:
        raise Unauthorized()
    request.state.user_id = str(user.id)
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
