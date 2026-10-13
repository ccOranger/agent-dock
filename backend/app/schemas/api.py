"""各业务接口的请求/响应 Schema"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ===== Auth =====
class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserInfo(BaseModel):
    id: str
    username: str
    role: str
    display_name: str | None = None

    @classmethod
    def from_orm_user(cls, u) -> "UserInfo":
        return cls(id=str(u.id), username=u.username, role=u.role, display_name=u.display_name)


class LoginResult(TokenPair):
    user: UserInfo


class RefreshRequest(BaseModel):
    refresh_token: str


# ===== Chat =====
class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)
    stream: bool = True


class ChatMessageRequest(ChatRequest):
    """非流式备用接口"""


# ===== Sessions =====
class SessionCreate(BaseModel):
    title: str = "新对话"


class SessionUpdate(BaseModel):
    title: str | None = None
    status: Literal["active", "closed", "handover"] | None = None


class SessionOut(BaseModel):
    id: str
    title: str
    status: str
    last_route_to: str | None = None
    created_at: datetime
    updated_at: datetime


# ===== Memory =====
class MemorySearchRequest(BaseModel):
    user_id: str | None = None
    query: str = Field(min_length=1)
    limit: int = Field(default=10, le=100)


class MemoryUpdateRequest(BaseModel):
    content: str | None = None
    importance: float | None = Field(default=None, ge=0, le=1)


# ===== Knowledge =====
class RagSearchRequest(BaseModel):
    namespace: str = "cs_default"
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, le=20)


class DocumentOut(BaseModel):
    id: str
    title: str
    rag_namespace: str
    status: str
    chunk_count: int
    content_type: str | None = None
    created_at: datetime


# ===== Admin =====
class AgentToggleRequest(BaseModel):
    enabled: bool
