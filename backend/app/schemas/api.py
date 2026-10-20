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
    # 路由线索：告知路由分发器“什么样的客户消息该选我”，不填则仅依 description 判定
    routing_keywords: list[str] = Field(default_factory=list, max_length=20)
    routing_examples: list[str] = Field(default_factory=list, max_length=5)
    system_prompt: str = Field(min_length=1)
    llm_model: str = Field(default="", max_length=128)  # 空则用默认 LLM_MODEL_ID
    rag_namespace: str | None = Field(default=None, max_length=64)
    rag_namespaces: list[str] = Field(default_factory=list)  # 显式多知识库（与 rag_namespace 合并去重）
    search_all_knowledge_bases: bool = False
    mcp_tool_names: list[str] = Field(default_factory=list)
    custom_tool_names: list[str] = Field(default_factory=list)  # 绑定的代码自定义工具（需在后台登记且启用）
    enabled: bool = True
    sort_order: int = 0


class DynamicAgentUpdate(BaseModel):
    """后台编辑动态 Agent（仅传入的字段会更新）"""

    display_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    routing_keywords: list[str] | None = Field(default=None, max_length=20)
    routing_examples: list[str] | None = Field(default=None, max_length=5)
    system_prompt: str | None = Field(default=None, min_length=1)
    llm_model: str | None = Field(default=None, max_length=128)
    rag_namespace: str | None = Field(default=None, max_length=64)
    rag_namespaces: list[str] | None = None
    search_all_knowledge_bases: bool | None = None
    mcp_tool_names: list[str] | None = None
    custom_tool_names: list[str] | None = None
    enabled: bool | None = None
    sort_order: int | None = None


class MCPServerCreate(BaseModel):
    """后台添加 MCP 服务（写入 tools 表 type=mcp）"""

    name: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
    description: str | None = Field(default=None, max_length=512)  # 该 MCP 服务的作用说明（仅后台展示，存于 config）
    transport: Literal["sse", "http", "stdio"] = "sse"
    endpoint: str | None = Field(default=None, max_length=512)  # sse/http 使用
    command: str | None = Field(default=None, max_length=256)  # stdio 使用
    args: list[str] = Field(default_factory=list)  # stdio 使用
    headers: dict | None = None
    auth: dict | None = None
    timeout_seconds: int = Field(default=15, ge=1, le=300)
    enabled: bool = True


class MCPServerUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=512)
    transport: Literal["sse", "http", "stdio"] | None = None
    endpoint: str | None = Field(default=None, max_length=512)
    command: str | None = Field(default=None, max_length=256)
    args: list[str] | None = None
    headers: dict | None = None
    auth: dict | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    enabled: bool | None = None


# ===== 管理后台：自定义工具登记 =====
class CustomToolRegister(BaseModel):
    """把代码目录里的工具登记到 tools 表（type=custom）；name 必须存在于代码目录。"""

    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=512)  # 可选覆盖显示描述（实现/参数仍以代码为准）
    enabled: bool = True


class CustomToolToggle(BaseModel):
    enabled: bool


# ===== 管理后台：高频固定话术（硬模板）=====
class CannedScriptCreate(BaseModel):
    """新增一条固定话术。种子三条只能改文案/启停，不可删除（见后台接口）。"""

    key: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    title: str | None = Field(default=None, max_length=128)
    category: Literal["identity", "capability", "probing", "business"] = "business"
    # 问法主干：归一化后整句全等才命中（不会误吞带后半句的业务问法）
    # 上限只是防手滑、不是安全边界：种子里的 probing 已经有 32 个关键词，
    # 所以两条上限必须高于种子规模，否则后台编辑种子时原样提交就会被 422 拒掉。
    patterns: list[str] = Field(default_factory=list, max_length=100)
    # 关键词：整句包含即命中，判定比问法宽，后台需要命中测试辅助把关
    keywords: list[str] = Field(default_factory=list, max_length=60)
    reply_text: str = Field(min_length=1, max_length=2000)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True


class CannedScriptUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=128)
    category: Literal["identity", "capability", "probing", "business"] | None = None
    patterns: list[str] | None = Field(default=None, max_length=100)
    keywords: list[str] | None = Field(default=None, max_length=60)
    reply_text: str | None = Field(default=None, min_length=1, max_length=2000)
    priority: int | None = Field(default=None, ge=0, le=1000)


class CannedScriptToggle(BaseModel):
    enabled: bool


class CannedScriptMatchRequest(BaseModel):
    """命中测试：不写库，只告知这句客户原话会落到哪条话术（或未命中）。"""

    query: str = Field(min_length=1, max_length=500)


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
