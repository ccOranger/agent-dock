"""SQLAlchemy ORM 模型：与 scripts/init_db.sql 对应"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.schemas.scene import DEFAULT_SCENE_ID


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Scene(Base, TimestampMixin):
    """对外业务场景：Agent 池、路由配置、话术与嵌入配置的隔离边界。"""

    __tablename__ = "scenes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    router_mode: Mapped[str] = mapped_column(String(16), default="auto")
    router_model: Mapped[str | None] = mapped_column(String(64))
    router_confidence_threshold: Mapped[float | None] = mapped_column(Float)
    router_max_intents: Mapped[int | None] = mapped_column(Integer)
    # 不建外键：与 agents.scene_id 形成循环；是否在池内由场景路由档案校验并降级
    fallback_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    fallback_text: Mapped[str | None] = mapped_column(Text)
    extra_router_prompt: Mapped[str | None] = mapped_column(Text)
    widget_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    allowed_origins: Mapped[list] = mapped_column(JSONB, default=list)
    publish_key: Mapped[str | None] = mapped_column(String(64))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class SceneAgent(Base):
    """场景 ↔ Agent；enabled 只影响本场景，不改变 Agent 全局状态。"""

    __tablename__ = "scene_agents"

    scene_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Integration(Base, TimestampMixin):
    """接入方：服务端调用的身份边界，持有 API Key 并被授权访问若干场景。

    Key 挂在接入方而不是场景上：场景是业务维度，接入方是调用方维度。
    同一场景被多个宿主接入时，靠接入方把访客身份命名空间分开。
    """

    __tablename__ = "integrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # 公开标识：嵌入页用 data-integration 传回，用于匿名访客归属与命名空间；
    # 会出现在宿主页面源码里，不是安全凭证，防滥用仍靠来源白名单与限流。
    public_code: Mapped[str] = mapped_column(String(32), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class IntegrationKey(Base):
    """接入方的服务端 API Key；只存 sha256，明文仅在签发响应里出现一次。

    允许一个接入方持有多把（轮换过渡期新旧并存），吊销即删除该行。
    """

    __tablename__ = "integration_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE")
    )
    key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    # 密钥尾号，后台列表用它识别是哪一把，不足以还原 key
    hint: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntegrationScene(Base):
    """接入方 ↔ 场景授权（多对多）：接入方只能调用被授权场景。"""

    __tablename__ = "integration_scenes"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE"), primary_key=True
    )
    scene_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Visitor(Base, TimestampMixin):
    """访客；浏览器匿名按场景隔离，服务端接入再按接入方隔离。"""

    __tablename__ = "visitors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"))
    # NULL=浏览器匿名（嵌入气泡签发）；非空=某个接入方下传的终端主体
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE")
    )
    external_key: Mapped[str | None] = mapped_column(String(128))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    __table_args__ = (
        CheckConstraint("(user_id IS NULL) <> (visitor_id IS NULL)", name="ck_sessions_actor"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # 默认场景：M2 之前的旧代码只写 user_id 时仍能落库；DB 侧也有同样的默认值
    scene_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenes.id", ondelete="RESTRICT"),
        default=lambda: uuid.UUID(DEFAULT_SCENE_ID),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer_users.id", ondelete="CASCADE"))
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visitors.id", ondelete="CASCADE"))
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
    # 唯一性由 init_db.sql 的 uq_memories_actor_uid（NULLS NOT DISTINCT）保证；
    # SQLAlchemy 2.0.35 的 UniqueConstraint 尚不支持 postgresql_nulls_not_distinct。
    __table_args__ = (
        CheckConstraint("(user_id IS NULL) <> (visitor_id IS NULL)", name="ck_memories_actor"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer_users.id", ondelete="CASCADE"))
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visitors.id", ondelete="CASCADE"))
    memory_uid: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"))
    # 溯源到产生该记忆的那条用户消息；随消息删除级联（注意：删会话会连带删掉其来源记忆行）
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
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
    content_type: Mapped[str | None] = mapped_column(String(128))  # docx 的 MIME 有 71 字符
    kind: Mapped[str] = mapped_column(String(16), default="upload")
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    rag_namespace: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class KnowledgeBase(Base, TimestampMixin):
    """知识库（命名空间）：全局资源，可被任意多个 Agent 绑定。

    业务区分交给命名与配置纪律，不在库上做场景归属。
    """

    __tablename__ = "knowledge_bases"

    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CannedScript(Base, TimestampMixin):
    """高频固定话术（硬模板）：命中则逐字下发，不进路由也不走模型生成。

    可后台维护的意义在于“新增一条营业时间回复”这类需求不应该再要求改代码发版，但
    匹配只开放两样东西（问法主干 + 关键词），不开放正则：正则交给运营容易写出灾难性回溯
    或过宽误吞的规则，而误吞的代价是把真实业务诉求用一句模板打发掉，比少命中严重得多。
    """

    __tablename__ = "canned_scripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"))
    # 业务可读标识；唯一性由 init_db.sql 的 (scene_id, key) NULLS NOT DISTINCT 保证
    key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(128))
    # 分组：identity | capability | probing | business（探听类在种子里给了最高优先级）
    category: Mapped[str] = mapped_column(String(16), default="business")
    # 问法主干（字符串数组）：归一化（去标点/语气词）后与整句全等才算命中，不会误吞复合问句
    patterns: Mapped[list] = mapped_column(JSONB, default=list)
    # 关键词（字符串数组）：整句里出现即命中，用于“提示词”“内部指令”这类强特征词
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    # 逐字下发的应答正文（支持 markdown，前端 StreamRenderer 已渲染）
    reply_text: Mapped[str] = mapped_column(Text, default="")
    # 小者优先；多条都能命中时决定哪一条胜出
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 种子三条（seed_data.py 建库时写入）：可改文案、可停用，但不可删除。运行时只读本表，
    # 代码里没有兜底规则，删掉探听拒答就等于把泄露面重新打开
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)


class AgentMeta(Base, TimestampMixin):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # NULL=全局；非空=场景自有。唯一性由 init_db.sql 的 (scene_id, name) NULLS NOT DISTINCT 保证
    scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    # ---- 路由线索：供路由 Agent 判定“何时选我”，代码/动态 Agent 均适用 ----
    # 触发关键词（字符串数组），如 ["发票", "开票", "税号"]
    routing_keywords: Mapped[list] = mapped_column(JSONB, default=list)
    # 典型用户问法（字符串数组），每个 2~3 条即可，用于帮模型建立语义映射
    routing_examples: Mapped[list] = mapped_column(JSONB, default=list)
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
    scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"))
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
    scene_code: Mapped[str] = mapped_column(String(32), default="default")
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
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
    router_skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    router_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    router_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_hash: Mapped[str | None] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(16))
    handover_reason: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    ttft_ms: Mapped[int | None] = mapped_column(Integer)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer)
    response_chars: Mapped[int] = mapped_column(Integer, default=0)

    spans: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatEvent(Base):
    """对话埋点：一行 = 一次聊天请求，全量记录，不受 request_traces 采样影响。

    ext 是客户端声明的自定义上下文（如 platform/channel），只用于统计与软策略；
    租户/接入方归属仍来自凭证，tenant_id 预留给多租户模式由鉴权层注入。
    """

    __tablename__ = "chat_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    scene_code: Mapped[str] = mapped_column(String(32), default="default")
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    integration_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # 预留给多租户：当前恒为 NULL，切换时由凭证解析后统一写入
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ext: Mapped[dict] = mapped_column(JSONB, default=dict)
    message_chars: Mapped[int] = mapped_column(Integer, default=0)
    # 预留：LLM usage 采集后回填，用于按接入方统计 token 用量
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SchemaMeta(Base):
    """schema 版本表；启动时断言，不匹配请全量重建。"""

    __tablename__ = "schema_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
