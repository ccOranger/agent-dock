"""鉴权接口：人员与客户分表后提供各自的登录入口，并支持客户自助注册"""
import ipaddress

from fastapi import APIRouter, Depends, Request

from app.api.deps import current_user
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
from app.models.repositories import AuditRepo, CustomerRepo, StaffRepo
from app.schemas.api import LoginRequest, LoginResult, RefreshRequest, RegisterRequest, UserInfo
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue(user) -> LoginResult:
    return LoginResult(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=create_refresh_token(str(user.id), user.role),
        expires_in=settings.access_token_ttl,
        user=UserInfo.from_orm_user(user),
    )


def _is_trusted_proxy(peer: str) -> bool:
    """直连对端是否在受信代理白名单内（支持单个 IP 与 CIDR）。"""
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for item in settings.trusted_proxy_list:
        try:
            if "/" in item:
                if peer_ip in ipaddress.ip_network(item, strict=False):
                    return True
            elif peer_ip == ipaddress.ip_address(item):
                return True
        except ValueError:
            continue
    return False


def _client_ip(request: Request) -> str:
    """取客户端 IP：仅当直连对端是受信代理时才采信 X-Forwarded-For 最左侧地址。

    无条件信任 XFF 会被伪造绕过限流；完全不看 XFF 时反向代理/NAT 后所有用户
    共享同一出口，错误密码 20 次就会锁死全站登录。
    """
    peer = request.client.host if request.client else "unknown"
    if _is_trusted_proxy(peer):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip() or peer
    return peer


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
    return ApiResponse(data=_issue(user))


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
    return ApiResponse(data=_issue(user))


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
    return ApiResponse(data=_issue(user))


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
    return ApiResponse(data=UserInfo.from_orm_user(user))
