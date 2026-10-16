"""SQLAlchemy ORM 模型：与 scripts/init_db.sql 对应"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StaffUser(Base, TimestampMixin):
    """后台人员（管理员 / 客服坐席），与客户数据物理隔离。"""

    __tablename__ = "staff_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="agent")
    display_name: Mapped[str | None] = mapped_column(String(64))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomerUser(Base, TimestampMixin):
    """客户账号，可自助注册，用于聊天会话与记忆归属。"""

    __tablename__ = "customer_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str | None] = mapped_column(String(64))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 供统一的 UserInfo / 依赖校验使用：客户角色恒为 "customer"
    role: str = "customer"


class Session(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(128), default="新对话")
    status: Mapped[str] = mapped_column(String(16), default="active")
    assigned_agent: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    memory_tool_uid: Mapped[str | None] = mapped_column(String(64))
    rag_namespace: Mapped[str | None] = mapped_column(String(64))
    last_route_to: Mapped[str | None] = mapped_column(String(64))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[dict | None] = mapped_column(JSONB)
    tool_call_id: Mapped[str | None] = mapped_column(String(64))
    routed_to: Mapped[str | None] = mapped_column(String(64))
    tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Memory(Base, TimestampMixin):
    __tablename__ = "memories"
    __table_args__ = (UniqueConstraint("user_id", "memory_uid", name="uq_memories_user_uid"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_users.id", ondelete="CASCADE"))
    memory_uid: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(256))
    source: Mapped[str | None] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="upload")
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    rag_namespace: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class KnowledgeBase(Base, TimestampMixin):
    __tablename__ = "knowledge_bases"

    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentMeta(Base, TimestampMixin):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # ---- 动态 Agent 支持：区分来源，dynamic 才可在后台增删改 ----
    # code：代码注册表托管（prompt/工具等在 Python 类里，后台仅能开关）
    # dynamic：完全由 DB 配置驱动，可在后台创建/编辑并配置 MCP 工具
    source: Mapped[str] = mapped_column(String(16), default="code")
    system_prompt: Mapped[str | None] = mapped_column(Text)
    llm_model: Mapped[str] = mapped_column(String(128), default="")
    rag_namespace: Mapped[str | None] = mapped_column(String(64))
    # 显式多知识库（与 rag_namespace 合并去重作为检索范围）；存 namespace 字符串数组
    rag_namespaces: Mapped[list] = mapped_column(JSONB, default=list)
    search_all_knowledge_bases: Mapped[bool] = mapped_column(Boolean, default=False)
    # Agent ↔ MCP 工具绑定（仅存名称，配置见 tools 表 type=mcp）
    mcp_tool_names: Mapped[list] = mapped_column(JSONB, default=list)
    # Agent ↔ 代码自定义工具绑定（仅存名称，实现来自代码目录 custom_catalog，登记见 tools 表 type=custom）
    custom_tool_names: Mapped[list] = mapped_column(JSONB, default=list)


class ToolMeta(Base, TimestampMixin):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(16))
    schema_: Mapped[dict] = mapped_column("schema", JSONB)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Ticket(Base, TimestampMixin):
    """客诉工单：结构化增改查的独立表。

    此前工单只作为 audit_logs 的 action=create_ticket/update_ticket 记录、把字段埋在
    payload JSONB 里，无法按状态流转、按工单号回查或在后台增改查。改为独立表：
    - ticket_no 对外可读（TK-XXXXXXXX），业务与后台都以它为主键回查；
    - user_id/session_id 存 UUID 但不建外键（示例客户名等非 UUID 会降级为 NULL），
      与 audit_logs 保持一致，避免历史脏数据阻塞写入；
    - updates 追加式记录每次补录（订单号/快递单号等），content 保留最新概述。
    """

    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    ticket_no: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    category: Mapped[str] = mapped_column(String(32), default="general")
    urgency: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="open")
    title: Mapped[str | None] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text, default="")
    updates: Mapped[list] = mapped_column(JSONB, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class RequestTrace(Base):
    """一轮对话的请求链路：经过哪些 Agent / MCP / 工具、耗时与结局。

    一行 = 一轮请求（一条用户消息），明细时间线存 spans；
    高频过滤维度拍平为标量/数组列，避免后台列表页解 JSONB。
    与 audit_logs 分表：后者是低频安全/操作审计，保留策略与查询模式均不同。
    """

    __tablename__ = "request_traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # HTTP 层 trace_id（structlog / X-Trace-Id），用于日志与链路互查
    trace_id: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # ---- 可过滤/排序的拍平维度 ----
    intent_count: Mapped[int] = mapped_column(Integer, default=0)
    agent_names: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    agent_sources: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    llm_models: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    mcp_server_names: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    tool_names: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    rag_namespaces: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    memory_hit_count: Mapped[int] = mapped_column(Integer, default=0)
    rag_chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_error_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float | None] = mapped_column(Float)
    outcome: Mapped[str | None] = mapped_column(String(16))
    handover_reason: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    ttft_ms: Mapped[int | None] = mapped_column(Integer)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer)
    response_chars: Mapped[int] = mapped_column(Integer, default=0)

    spans: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
