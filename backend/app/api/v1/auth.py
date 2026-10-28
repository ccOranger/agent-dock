"""鉴权接口：人员与客户分表后提供各自的登录入口，并支持客户自助注册"""
from fastapi import APIRouter, Depends, Request

from app.api.deps import current_user, effective_permissions
from app.core.config import settings
from app.core.exceptions import (
    AccountNotRegistered,
    Conflict,
    ParamError,
    Unauthorized,
)
from app.core.login_guard import (
    check_login_allowed,
    clear_login_failures,
    record_login_failure,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.repositories import AuditRepo, CustomerRepo, RoleRepo, StaffRepo
from app.schemas.api import LoginRequest, LoginResult, RefreshRequest, RegisterRequest, UserInfo
from app.schemas.common import ApiResponse
from app.utils.request import client_ip as _client_ip

router = APIRouter(prefix="/auth", tags=["auth"])


async def _issue(user) -> LoginResult:
    return LoginResult(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=create_refresh_token(str(user.id), user.role),
        expires_in=settings.access_token_ttl,
        user=await build_user_info(user),
    )


async def build_user_info(user) -> UserInfo:
    """拼上 RBAC：后台人员额外携带角色 key 列表与有效菜单权限（供前端过滤与配置）。"""
    info = UserInfo.from_orm_user(user)
    if user.role in ("agent", "admin"):
        info.roles = await RoleRepo.get_user_role_keys(user.id) or [user.role]
        info.permissions = await effective_permissions(user)
    return info


@router.post("/staff/login", response_model=ApiResponse[LoginResult])
async def staff_login(body: LoginRequest, request: Request):
    """后台人员登录（管理后台 admin-frontend 使用）。"""
    ip = _client_ip(request)
    # 先判限流：防止窗口内高频试错
    check_login_allowed(ip, body.username)
    user = await StaffRepo.get_by_username(body.username)
    if user is None or not verify_password(body.password, user.password_hash):
        record_login_failure(ip, body.username)
        await AuditRepo.write(action="login_failed", payload={"username": body.username}, ip=ip)
        raise ParamError("用户名或密码错误")
    clear_login_failures(body.username)
    if not user.is_active:
        raise Unauthorized("账号已停用")
    await AuditRepo.write(action="login", user_id=user.id, ip=ip)
    return ApiResponse(data=await _issue(user))


@router.post("/customer/login", response_model=ApiResponse[LoginResult])
async def customer_login(body: LoginRequest, request: Request):
    """客户登录（客户端 frontend 使用）。账号不存在时返回可识别错误码以引导注册。"""
    ip = _client_ip(request)
    check_login_allowed(ip, body.username)
    user = await CustomerRepo.get_by_username(body.username)
    if user is None:
        # 账号不存在也计入失败：避免被用来批量枚举用户名
        record_login_failure(ip, body.username)
        raise AccountNotRegistered()
    if not verify_password(body.password, user.password_hash):
        record_login_failure(ip, body.username)
        await AuditRepo.write(action="login_failed", payload={"username": body.username}, ip=ip)
        raise ParamError("用户名或密码错误")
    clear_login_failures(body.username)
    if not user.is_active:
        raise Unauthorized("账号已停用")
    await AuditRepo.write(action="login", user_id=user.id, ip=ip)
    return ApiResponse(data=await _issue(user))


@router.post("/customer/register", response_model=ApiResponse[LoginResult])
async def customer_register(body: RegisterRequest, request: Request):
    """客户自助注册，成功后直接下发 Token 自动登录。"""
    exists = await CustomerRepo.get_by_username(body.username)
    if exists is not None:
        raise Conflict("该用户名已被注册")
    user = await CustomerRepo.create(
        username=body.username,
        password_hash=hash_password(body.password),
        display_name=body.username,
    )
    await AuditRepo.write(action="customer_register", user_id=user.id, ip=request.client.host)
    return ApiResponse(data=await _issue(user))


@router.post("/refresh", response_model=ApiResponse[dict])
async def refresh(body: RefreshRequest):
    payload = decode_token(body.refresh_token)
    if payload is None or payload.get("typ") != "refresh":
        raise Unauthorized("refresh token 无效")
    role = payload.get("role")
    sub = payload.get("sub")
    if role == "customer":
        user = await CustomerRepo.get_by_id(sub)
    elif role in ("agent", "admin"):
        user = await StaffRepo.get_by_id(sub)
    else:
        raise Unauthorized()
    if user is None or not user.is_active:
        raise Unauthorized()
    return ApiResponse(
        data={
            "access_token": create_access_token(str(user.id), user.role),
            "refresh_token": create_refresh_token(str(user.id), user.role),
            "token_type": "bearer",
            "expires_in": settings.access_token_ttl,
        }
    )


@router.get("/me", response_model=ApiResponse[UserInfo])
async def me(user=Depends(current_user)):
    return ApiResponse(data=await build_user_info(user))
