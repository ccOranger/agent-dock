-- 智能客服系统初始化 SQL（对应 Spec 4.1）
-- 说明：默认 UUID 使用 PG13+ 内置的 gen_random_uuid()，无需 uuid-ossp 扩展；
--       本 schema 未使用 vector 列（RAG 向量存于 Qdrant），故不依赖 pgvector。
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ========== 后台人员（管理员 / 客服坐席） ==========
CREATE TABLE staff_users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(64) UNIQUE NOT NULL,
    password_hash   VARCHAR(256) NOT NULL,
    role            VARCHAR(16) NOT NULL DEFAULT 'agent'
                    CHECK (role IN ('agent','admin')),
    display_name    VARCHAR(64),
    metadata        JSONB DEFAULT '{}'::jsonb,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_staff_users_role ON staff_users(role);

-- ========== 客户账号（可自助注册） ==========
CREATE TABLE customer_users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(64) UNIQUE NOT NULL,
    password_hash   VARCHAR(256) NOT NULL,
    display_name    VARCHAR(64),
    metadata        JSONB DEFAULT '{}'::jsonb,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========== 会话 ==========
CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES customer_users(id) ON DELETE CASCADE,
    title           VARCHAR(128) DEFAULT '新对话',
    status          VARCHAR(16) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','closed','handover')),
    assigned_agent  UUID REFERENCES staff_users(id),
    memory_tool_uid VARCHAR(64),
    rag_namespace   VARCHAR(64),
    last_route_to   VARCHAR(64),
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sessions_user_created ON sessions(user_id, created_at DESC);

-- ========== 消息 ==========
CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role            VARCHAR(16) NOT NULL
                    CHECK (role IN ('user','assistant','system','tool')),
    content         TEXT NOT NULL DEFAULT '',
    tool_calls      JSONB,
    tool_call_id    VARCHAR(64),
    routed_to       VARCHAR(64),
    tokens          INT,
    latency_ms      INT,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_session_created ON messages(session_id, created_at ASC);

-- ========== 记忆归档（MemoryTool 的镜像） ==========
CREATE TABLE memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES customer_users(id) ON DELETE CASCADE,
    memory_uid      VARCHAR(64) NOT NULL,
    session_id      UUID REFERENCES sessions(id) ON DELETE SET NULL,
    type            VARCHAR(16) NOT NULL
                    CHECK (type IN ('working','episodic','semantic','perceptual')),
    content         TEXT NOT NULL,
    summary         TEXT,
    importance      REAL NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
    access_count    INT NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMPTZ,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, memory_uid)
);
CREATE INDEX idx_memories_user_type ON memories(user_id, type);
CREATE INDEX idx_memories_importance ON memories(importance DESC);
CREATE INDEX idx_memories_content_trgm ON memories USING gin (content gin_trgm_ops);

-- ========== 知识库命名空间（管理后台维护） ==========
CREATE TABLE knowledge_bases (
    namespace     VARCHAR(64) PRIMARY KEY,
    display_name  VARCHAR(128) NOT NULL,
    description   TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========== 知识库文档归档 ==========
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           VARCHAR(256) NOT NULL,
    source          VARCHAR(512),
    content_type    VARCHAR(64),
    size_bytes      BIGINT,
    kind              VARCHAR(16) NOT NULL DEFAULT 'upload'
                    CHECK (kind IN ('upload','manual')),
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','processing','ready','failed')),
    error_message   TEXT,
    rag_namespace   VARCHAR(64) NOT NULL,
    chunk_count     INT NOT NULL DEFAULT 0,
    uploaded_by     UUID REFERENCES staff_users(id),
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_namespace ON documents(rag_namespace);
CREATE INDEX idx_documents_kind ON documents(kind);

-- 知识库命名空间种子（与现有 agent rag_namespace 一致）
INSERT INTO knowledge_bases (namespace, display_name, description) VALUES
    ('cs_default', '通用知识库', '默认通用客服知识'),
    ('cs_order', '订单知识库', '订单相关业务'),
    ('cs_after_sales', '售后知识库', '售后/投诉相关业务')
ON CONFLICT (namespace) DO NOTHING;

-- ========== Agent 元数据注册（代码托管 + 动态后台创建） ==========
CREATE TABLE agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(64) UNIQUE NOT NULL,
    display_name    VARCHAR(128),
    description     TEXT,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order      INT DEFAULT 0,
    source          VARCHAR(16) NOT NULL DEFAULT 'code',   -- code | dynamic
    system_prompt   TEXT,                                   -- dynamic 必填
    llm_model       VARCHAR(128) NOT NULL DEFAULT '',       -- 空则用默认 LLM_MODEL_ID
    rag_namespace   VARCHAR(64),
    search_all_knowledge_bases BOOLEAN NOT NULL DEFAULT FALSE,
    mcp_tool_names  JSONB NOT NULL DEFAULT '[]'::jsonb,     -- Agent ↔ MCP 工具绑定
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========== 工具注册 ==========
CREATE TABLE tools (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(64) UNIQUE NOT NULL,
    type            VARCHAR(16) NOT NULL CHECK (type IN ('mcp','custom','builtin')),
    schema          JSONB NOT NULL,
    config          JSONB DEFAULT '{}'::jsonb,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========== 审计日志 ==========
CREATE TABLE audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID,
    session_id      UUID,
    action          VARCHAR(64) NOT NULL,
    target_type     VARCHAR(32),
    target_id       VARCHAR(64),
    payload         JSONB,
    ip              VARCHAR(64),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_user_created ON audit_logs(user_id, created_at DESC);
CREATE INDEX idx_audit_action ON audit_logs(action);

-- ========== 请求链路（一轮对话一行，明细存 spans） ==========
CREATE TABLE IF NOT EXISTS request_traces (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id            VARCHAR(64) NOT NULL,       -- 与 structlog / X-Trace-Id 一致
    session_id          UUID,
    user_id             UUID,
    user_message_id     UUID,
    assistant_message_id UUID,
    -- 可过滤的拍平维度
    intent_count        INT NOT NULL DEFAULT 0,
    agent_names         TEXT[] DEFAULT '{}',
    agent_sources       TEXT[] DEFAULT '{}',
    llm_models          TEXT[] DEFAULT '{}',
    mcp_server_names    TEXT[] DEFAULT '{}',
    tool_names          TEXT[] DEFAULT '{}',
    rag_namespaces      TEXT[] DEFAULT '{}',
    memory_hit_count    INT NOT NULL DEFAULT 0,
    rag_chunk_count     INT NOT NULL DEFAULT 0,
    tool_call_count     INT NOT NULL DEFAULT 0,
    tool_error_count    INT NOT NULL DEFAULT 0,
    confidence          REAL,
    outcome             VARCHAR(16),               -- answered | handover | error
    handover_reason     VARCHAR(32),
    error_code          INT,
    error_message       TEXT,
    ttft_ms             INT,
    total_latency_ms    INT,
    response_chars      INT NOT NULL DEFAULT 0,
    spans               JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_traces_trace_id ON request_traces(trace_id);
-- 咨询详情按助手消息 ID 反查链路（messages LEFT JOIN request_traces）
CREATE INDEX IF NOT EXISTS idx_traces_assistant_msg ON request_traces(assistant_message_id);
CREATE INDEX IF NOT EXISTS idx_traces_session_created ON request_traces(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_user_created ON request_traces(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_outcome_created ON request_traces(outcome, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_agents ON request_traces USING gin (agent_names);
CREATE INDEX IF NOT EXISTS idx_traces_tools ON request_traces USING gin (tool_names);
CREATE INDEX IF NOT EXISTS idx_traces_mcp ON request_traces USING gin (mcp_server_names);
