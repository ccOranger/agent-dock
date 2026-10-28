"""各业务接口的请求/响应 Schema"""
import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# 自定义埋点上下文：扁平标量、限键限长，避免客户端把 JSON 当垃圾桶
EXT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
EXT_MAX_KEYS = 10
EXT_MAX_VALUE_CHARS = 128
EXT_MAX_BYTES = 1024
EXT_BLOCKED_KEYS = {
    "token",
    "password",
    "authorization",
    "secret",
    "api_key",
    "access_token",
    "refresh_token",
}


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
    # RBAC：登录人员所属角色 key 列表与有效菜单权限（{menu_key: 'read'|'write'}）
    roles: list[str] = []
    permissions: dict[str, str] = {}

    @classmethod
    def from_orm_user(cls, u) -> "UserInfo":
        return cls(id=str(u.id), username=u.username, role=u.role, display_name=u.display_name)


class LoginResult(TokenPair):
    user: UserInfo


class RefreshRequest(BaseModel):
    refresh_token: str


# ===== Chat =====
class ChatRequest(BaseModel):
    # 多场景：缺省走默认场景；错码/停用由服务层返回 3003/3004
    scene_code: str | None = Field(default=None, max_length=32)
    session_id: str | None = None
    # 匿名访客 ID：首次由服务端签发；已登录用户忽略
    visitor_id: str | None = Field(default=None, max_length=36)
    # 客户端重试去重键（P1）
    client_msg_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=4000)
    stream: bool = True
    # 自定义埋点上下文（扁平 JSON）：渠道等设备信息也统一放进来，原样存 ext
    ext: dict[str, str | int | bool] | None = None

    @field_validator("ext")
    @classmethod
    def _validate_ext(cls, value: dict | None) -> dict | None:
        if value is None:
            return None
        if len(value) > EXT_MAX_KEYS:
            raise ValueError(f"ext 最多 {EXT_MAX_KEYS} 个字段")
        cleaned: dict[str, str | int | bool] = {}
        for key, item in value.items():
            if not EXT_KEY_RE.match(str(key)):
                raise ValueError(f"ext 字段名不合法: {key}")
            if str(key).lower() in EXT_BLOCKED_KEYS:
                raise ValueError(f"ext 不允许包含敏感字段: {key}")
            if isinstance(item, bool):
                cleaned[key] = item
            elif isinstance(item, int):
                cleaned[key] = item
            elif isinstance(item, str):
                if len(item) > EXT_MAX_VALUE_CHARS:
                    raise ValueError(f"ext.{key} 超长（最多 {EXT_MAX_VALUE_CHARS} 字符）")
                cleaned[key] = item
            else:
                raise ValueError(f"ext.{key} 只支持字符串/整数/布尔值")
        if len(json.dumps(cleaned, ensure_ascii=False)) > EXT_MAX_BYTES:
            raise ValueError(f"ext 序列化后超过 {EXT_MAX_BYTES} 字节")
        return cleaned


class ChatMessageRequest(ChatRequest):
    """非流式备用接口"""


# ===== Sessions =====
class SessionCreate(BaseModel):
    title: str = "新对话"
    # 缺省落到默认场景；会话列表/详情/删除均按 (场景, 客户) 隔离
    scene_code: str | None = Field(default=None, max_length=32)


class SessionUpdate(BaseModel):
    title: str | None = None
    status: Literal["active", "closed", "handover", "human_handling"] | None = None


class SessionOut(BaseModel):
    id: str
    title: str
    status: str
    last_route_to: str | None = None
    created_at: datetime
    updated_at: datetime


# ===== 人工接管（admin takeover） =====
class TakeoverMessageRequest(BaseModel):
    """坐席回复：与机器人应答同长度上限，避免超长文本刷屏。"""

    content: str = Field(min_length=1, max_length=4000)


class TakeoverReleaseRequest(BaseModel):
    """释放接管：to=bot 交还机器人，to=close 结束会话；缺省交还机器人。"""

    to: Literal["bot", "close"] = "bot"


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
    # 归属场景：不传=全局话术（如 probing），传了在本场景内覆盖同 key 全局行
    scene_code: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=128)
    category: Literal["identity", "capability", "probing", "business"] = "business"
    # 问法主干：归一化后整句全等才命中（不会误吞带后半句的业务问法）
    # 上限只是防手滑、不是安全边界：种子里的 probing 已经有 32 个关键词，
    # 所以两条上限必须高于种子规模，否则后台编辑种子时原样提交就会被 422 拒掉。
    patterns: list[str] = Field(default_factory=list, max_length=100)
    # 关键词：整句包含即命中，判定比问法宽，后台需要命中测试辅助把关
    keywords: list[str] = Field(default_factory=list, max_length=60)
    # 文本模式必填；kb 模式只当引导语/兜底文案，因此允许为空
    reply_text: str = Field(default="", max_length=2000)
    # 回答来源：text=逐字下发 reply_text；kb=检索知识库后按固定框架拼接（不走模型改写）
    answer_source: Literal["text", "kb"] = "text"
    kb_namespaces: list[str] = Field(default_factory=list, max_length=10)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True


class CannedScriptUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=128)
    category: Literal["identity", "capability", "probing", "business"] | None = None
    patterns: list[str] | None = Field(default=None, max_length=100)
    keywords: list[str] | None = Field(default=None, max_length=60)
    reply_text: str | None = Field(default=None, max_length=2000)
    answer_source: Literal["text", "kb"] | None = None
    kb_namespaces: list[str] | None = Field(default=None, max_length=10)
    priority: int | None = Field(default=None, ge=0, le=1000)


class CannedScriptToggle(BaseModel):
    enabled: bool


class CannedScriptMatchRequest(BaseModel):
    """命中测试：不写库，只告知这句客户原话会落到哪条话术（或未命中）。"""

    query: str = Field(min_length=1, max_length=500)
    scene_code: str | None = Field(default=None, max_length=32)


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
    """知识库是全局资源：不记场景归属，同库可被多个 Agent 绑定。"""

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


# ===== 管理后台：人员与配额 =====
class StaffCreate(BaseModel):
    """新建后台人员：密码强度由登录环节同一套校验负责，这里只限长度。"""

    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    role: Literal["admin", "agent"] = "agent"
    # 多角色：传入则整体覆盖角色集合；不传则按 role 单角色起步
    roles: list[str] | None = Field(default=None, max_length=16)
    display_name: str | None = Field(default=None, max_length=64)
    # 键缺失/ null = 不限，0 = 禁止创建，正数 = 上限
    quota: dict[str, int | None] | None = None


class StaffUpdate(BaseModel):
    """局部更新；quota 传了就是整对象替换（传 {} 清空全部限制）。"""

    display_name: str | None = Field(default=None, max_length=64)
    role: Literal["admin", "agent"] | None = None
    # 传了即整体替换角色集合（primary role 列随之同步）
    roles: list[str] | None = Field(default=None, max_length=16)
    is_active: bool | None = None
    quota: dict[str, int | None] | None = None


class StaffPasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=128)


# ===== 管理后台：角色与菜单权限（RBAC）=====
class RoleOut(BaseModel):
    id: str
    key: str
    name: str
    description: str | None = None
    is_system: bool
    permissions: dict[str, str] = {}
    user_count: int = 0


class RoleCreate(BaseModel):
    key: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permissions: dict[str, Literal["read", "write"]] | None = None


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)


class PermissionGrant(BaseModel):
    """整体替换角色授权矩阵：{menu_key: 'read'|'write'}；非法 key 在接口层过滤。"""

    permissions: dict[str, Literal["read", "write"]]
