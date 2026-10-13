"""鉴权接口"""
from fastapi import APIRouter, Depends, Request

from app.api.deps import current_user
from app.core.config import settings
from app.core.exceptions import ParamError, Unauthorized
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.models.repositories import AuditRepo, UserRepo
from app.schemas.api import LoginRequest, LoginResult, RefreshRequest, UserInfo
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=ApiResponse[LoginResult])
async def login(body: LoginRequest, request: Request):
    user = await UserRepo.get_by_username(body.username)
    if user is None or not verify_password(body.password, user.password_hash):
        await AuditRepo.write(action="login_failed", payload={"username": body.username}, ip=request.client.host)
        raise ParamError("用户名或密码错误")
    await AuditRepo.write(action="login", user_id=user.id, ip=request.client.host)
    data = LoginResult(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=create_refresh_token(str(user.id), user.role),
        expires_in=settings.access_token_ttl,
        user=UserInfo.from_orm_user(user),
    )
    return ApiResponse(data=data)


@router.post("/refresh", response_model=ApiResponse[dict])
async def refresh(body: RefreshRequest):
    payload = decode_token(body.refresh_token)
    if payload is None or payload.get("typ") != "refresh":
        raise Unauthorized("refresh token 无效")
    user = await UserRepo.get_by_id(payload["sub"])
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
