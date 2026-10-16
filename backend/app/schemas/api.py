"""各业务接口的请求/响应 Schema"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ===== Auth =====
class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)


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
    advanced: bool = True
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)


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


class DynamicAgentCreate(BaseModel):
    """后台创建动态 Agent"""

    name: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
    display_name: str | None = Field(default=None, max_length=128)
    description: str = Field(min_length=1, max_length=512)
    system_prompt: str = Field(min_length=1)
    llm_model: str = Field(default="", max_length=128)  # 空则用默认 LLM_MODEL_ID
    rag_namespace: str | None = Field(default=None, max_length=64)
    search_all_knowledge_bases: bool = False
    mcp_tool_names: list[str] = Field(default_factory=list)
    enabled: bool = True
    sort_order: int = 0


class DynamicAgentUpdate(BaseModel):
    """后台编辑动态 Agent（仅传入的字段会更新）"""

    display_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    system_prompt: str | None = Field(default=None, min_length=1)
    llm_model: str | None = Field(default=None, max_length=128)
    rag_namespace: str | None = Field(default=None, max_length=64)
    search_all_knowledge_bases: bool | None = None
    mcp_tool_names: list[str] | None = None
    enabled: bool | None = None
    sort_order: int | None = None


class MCPServerCreate(BaseModel):
    """后台添加 MCP 服务（写入 tools 表 type=mcp）"""

    name: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
    transport: Literal["sse", "http", "stdio"] = "sse"
    endpoint: str | None = Field(default=None, max_length=512)  # sse/http 使用
    command: str | None = Field(default=None, max_length=256)  # stdio 使用
    args: list[str] = Field(default_factory=list)  # stdio 使用
    headers: dict | None = None
    auth: dict | None = None
    timeout_seconds: int = Field(default=15, ge=1, le=300)
    enabled: bool = True


class MCPServerUpdate(BaseModel):
    transport: Literal["sse", "http", "stdio"] | None = None
    endpoint: str | None = Field(default=None, max_length=512)
    command: str | None = Field(default=None, max_length=256)
    args: list[str] | None = None
    headers: dict | None = None
    auth: dict | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    enabled: bool | None = None


# ===== 管理后台：咨询信息 =====
class InquirySessionOut(BaseModel):
    id: str
    title: str
    status: str
    last_route_to: str | None = None
    user_id: str
    username: str
    display_name: str | None = None
    message_count: int = 0
    created_at: str
    updated_at: str


class InquiryDetailOut(BaseModel):
    session: InquirySessionOut
    messages: list[dict]


# ===== 管理后台：知识库维护 =====
class KnowledgeBaseCreate(BaseModel):
    namespace: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
    display_name: str = Field(min_length=1, max_length=128)
    description: str | None = None


class KnowledgeBaseUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    description: str | None = None
    is_active: bool | None = None


class KnowledgeBaseOut(BaseModel):
    namespace: str
    display_name: str
    description: str | None = None
    is_active: bool
    document_count: int = 0


class KnowledgeEntryCreate(BaseModel):
    namespace: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1)


class KnowledgeEntryUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    content: str = Field(min_length=1)


# ===== 管理后台：工单 =====
class TicketStatusUpdate(BaseModel):
    status: Literal["open", "in_progress", "escalated", "resolved", "closed"]
    note: str | None = Field(default=None, max_length=2000)


class TicketNoteCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)