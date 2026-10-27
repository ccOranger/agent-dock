"""API 依赖：认证、RBAC（人员 / 客户分表后按 role 路由到对应仓储）"""
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import Forbidden, TokenExpired, Unauthorized
from app.core.license import FEATURE_CATALOG, has_feature
from app.core.security import decode_token
from app.models.repositories import CustomerRepo, StaffRepo

bearer_scheme = HTTPBearer(auto_error=False)

STAFF_ROLES = ("agent", "admin")


async def _load_user(creds: HTTPAuthorizationCredentials, request: Request):
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
    return await _load_user(creds, request)


async def optional_customer(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """聊天入口用：没带 Authorization 返回 None（后续按公开接入/访客处理）。

    带了但无效仍然报错：不能把「Token 过期」静默降级成匿名访客，
    否则客户会在不知情的情况下丢掉自己的会话与记忆。
    """
    if creds is None:
        return None
    user = await _load_user(creds, request)
    if user.role != "customer":
        raise Forbidden()
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


def require_feature(feature: str):
    """License 功能位依赖工厂（rbac / audit / white_label / eval_pro / sso）。

    只负责功能位，不隐含角色：挂在自己已限定角色的路由上即可，例如
    ``Depends(require_feature("audit"))``。社区版一律 403，但对话与其余
    后台功能不受影响（只降级不阻断）；企业功能实现本身不在开源仓库。
    """

    async def _dep(user=Depends(current_user)):
        if not has_feature(feature):
            label = (FEATURE_CATALOG.get(feature) or {}).get("label", feature)
            raise Forbidden(f"当前版本未包含「{label}」功能，如需使用请联系升级授权")
        return user

    return _dep
