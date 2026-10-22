-- 智能客服系统初始化 SQL（唯一 DDL 源，对应 Spec 4.1）
-- 说明：
--   1) 默认 UUID 使用 PG13+ 内置的 gen_random_uuid()，无需 uuid-ossp 扩展；
--   2) 本 schema 未使用 vector 列（RAG 向量存于 Qdrant），故不依赖 pgvector；
--   3) 目标库为 PostgreSQL 15+（docker-compose 使用 pgvector/pgvector:pg16）；
--      agents/canned_scripts/memories 的唯一约束使用 UNIQUE NULLS NOT DISTINCT；
--   4) 本文件只保留最新 schema，不再包含历史 ALTER/回填段；
--   5) init_db.py 按分号切分执行，禁止 DO $$ 块、函数体或存储过程。
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ========== 场景（对外业务隔离边界） ==========
-- fallback_agent_id 不建外键：agents.scene_id 反向引用 scenes，两者会形成循环。
-- 兜底 Agent 是否在场景池内，由 SceneRouterProfile 在读取时校验并降级。
CREATE TABLE scenes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(32) UNIQUE NOT NULL,          -- 对外场景码，创建后不可改
    display_name    VARCHAR(64) NOT NULL,
    description     TEXT,
    status          VARCHAR(16) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','paused','archived')),
    is_default      BOOLEAN NOT NULL DEFAULT FALSE,
    router_mode     VARCHAR(16) NOT NULL DEFAULT 'auto'
                    CHECK (router_mode IN ('auto','llm','direct')),
    router_model    VARCHAR(64),                          -- NULL=继承全局 ROUTER_MODEL
    router_confidence_threshold DOUBLE PRECISION
                    CHECK (router_confidence_threshold IS NULL
                           OR router_confidence_threshold BETWEEN 0 AND 1),
    router_max_intents INT
                    CHECK (router_max_intents IS NULL OR router_max_intents BETWEEN 1 AND 10),
    fallback_agent_id UUID,                               -- 不建外键，见上方说明
    fallback_text   TEXT,
    extra_router_prompt TEXT,                             -- 场景补充识别规则
    widget_config   JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 标题/欢迎语/主题/位置等
    allowed_origins JSONB NOT NULL DEFAULT '[]'::jsonb,   -- 嵌入允许来源
    publish_key     VARCHAR(64),                          -- 前端嵌入公开标识（非密钥）
    api_key_hash    VARCHAR(128),                         -- 服务端 API key 哈希
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 全库只能有一个默认场景；默认场景由初始化种子写入，只读
CREATE UNIQUE INDEX IF NOT EXISTS uq_scenes_single_default ON scenes(is_default) WHERE is_default;
CREATE UNIQUE INDEX IF NOT EXISTS uq_scenes_publish_key ON scenes(publish_key) WHERE publish_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_scenes_status ON scenes(status);

-- 默认场景：无 scene_code 的请求落到这里。
-- 固定 UUID 便于初始化脚本、排障脚本与文档引用；改动需同步 backend/app/schemas/scene.py。
INSERT INTO scenes (id, code, display_name, description, is_default, router_mode)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'default',
    '默认场景',
    '无 scene_code 时的默认落点；单 Agent 直连，只读',
    TRUE,
    'direct'
)
ON CONFLICT DO NOTHING;

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
CREATE INDEX IF NOT EXISTS idx_staff_users_role ON staff_users(role);

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

-- ========== 访客（匿名接入，按场景隔离） ==========
CREATE TABLE visitors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id        UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    external_key    VARCHAR(128),                         -- 宿主页面可传入的稳定标识（可选）
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_seen_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- external_key 可空：空表示不做宿主侧归并，同一场景允许多个 NULL；
    -- 非空时同一场景内唯一。默认 NULLS DISTINCT，正是这里需要的语义。
    CONSTRAINT uq_visitors_scene_external UNIQUE (scene_id, external_key)
);
CREATE INDEX IF NOT EXISTS idx_visitors_scene_last_seen ON visitors(scene_id, last_seen_at DESC);

-- ========== Agent 元数据注册（代码托管 + 动态后台创建） ==========
CREATE TABLE agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id        UUID REFERENCES scenes(id) ON DELETE CASCADE,  -- NULL=全局共享
    name            VARCHAR(64) NOT NULL,
    display_name    VARCHAR(128),
    description     TEXT,
    routing_keywords JSONB NOT NULL DEFAULT '[]'::jsonb,    -- 路由线索：触发关键词
    routing_examples JSONB NOT NULL DEFAULT '[]'::jsonb,    -- 路由线索：典型问法
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order      INT DEFAULT 0,
    source          VARCHAR(16) NOT NULL DEFAULT 'code'
                    CHECK (source IN ('code','dynamic')),
    system_prompt   TEXT,                                   -- dynamic 必填
    llm_model       VARCHAR(128) NOT NULL DEFAULT '',       -- 空则用默认 LLM_MODEL_ID
    rag_namespace   VARCHAR(64),
    rag_namespaces  JSONB NOT NULL DEFAULT '[]'::jsonb,      -- 显式多知识库
    search_all_knowledge_bases BOOLEAN NOT NULL DEFAULT FALSE,
    mcp_tool_names  JSONB NOT NULL DEFAULT '[]'::jsonb,     -- Agent ↔ MCP 工具绑定
    custom_tool_names JSONB NOT NULL DEFAULT '[]'::jsonb,   -- Agent ↔ 代码自定义工具绑定
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 场景内同名；scene_id NULL 表示全局共享，NULLS NOT DISTINCT 保证全局名也唯一
    CONSTRAINT uq_agents_scene_name UNIQUE NULLS NOT DISTINCT (scene_id, name),
    -- 代码 Agent 是内核能力，必须全局注册，不能挂在某个场景下
    CONSTRAINT ck_agents_code_global CHECK (source <> 'code' OR scene_id IS NULL)
);

-- ========== 场景 ↔ Agent（多对多；enabled 只影响本场景） ==========
CREATE TABLE scene_agents (
    scene_id        UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order      INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (scene_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_scene_agents_scene_enabled
    ON scene_agents(scene_id, enabled, sort_order);

-- ========== 会话 ==========
-- scene_id 带默认值：M2 之前的旧代码只写 user_id，仍能落到默认场景。
-- user_id / visitor_id 二选一：已登录客户走 user，匿名嵌入走 visitor。
CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001'
                    REFERENCES scenes(id) ON DELETE RESTRICT,
    user_id         UUID REFERENCES customer_users(id) ON DELETE CASCADE,
    visitor_id      UUID REFERENCES visitors(id) ON DELETE CASCADE,
    title           VARCHAR(128) DEFAULT '新对话',
    status          VARCHAR(16) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','closed','handover')),
    assigned_agent  UUID REFERENCES staff_users(id),
    memory_tool_uid VARCHAR(64),
    rag_namespace   VARCHAR(64),
    last_route_to   VARCHAR(64),
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_sessions_actor CHECK ((user_id IS NULL) <> (visitor_id IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_sessions_scene_created ON sessions(scene_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user_created ON sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_visitor_created ON sessions(visitor_id, created_at DESC);

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
CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at ASC);

-- ========== 记忆归档（MemoryTool 的镜像） ==========
-- 与 sessions 相同的 actor 模型：user_id / visitor_id 二选一。
CREATE TABLE memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES customer_users(id) ON DELETE CASCADE,
    visitor_id      UUID REFERENCES visitors(id) ON DELETE CASCADE,
    memory_uid      VARCHAR(64) NOT NULL,
    session_id      UUID REFERENCES sessions(id) ON DELETE SET NULL,
    source_message_id UUID REFERENCES messages(id) ON DELETE CASCADE,
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
    CONSTRAINT ck_memories_actor CHECK ((user_id IS NULL) <> (visitor_id IS NULL)),
    -- 用户侧 visitor_id 为 NULL、访客侧 user_id 为 NULL，NULLS NOT DISTINCT 保证两类都唯一
    CONSTRAINT uq_memories_actor_uid UNIQUE NULLS NOT DISTINCT (user_id, visitor_id, memory_uid)
);
CREATE INDEX IF NOT EXISTS idx_memories_user_type ON memories(user_id, type);
CREATE INDEX IF NOT EXISTS idx_memories_visitor_type ON memories(visitor_id, type);
CREATE INDEX IF NOT EXISTS idx_memories_source_message ON memories(source_message_id);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_content_trgm ON memories USING gin (content gin_trgm_ops);

-- ========== 知识库命名空间（管理后台维护） ==========
-- 知识库是全局资源：不记场景归属，同库可被多个 Agent 绑定；
-- 业务区分交给 namespace 命名与配置纪律。
-- 旧库升级（可选，代码已不再读写该列）：ALTER TABLE knowledge_bases DROP COLUMN scene_id;
CREATE TABLE knowledge_bases (
    namespace     VARCHAR(64) PRIMARY KEY,
    display_name  VARCHAR(128) NOT NULL,
    description   TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 知识库命名空间种子（与现有代码 Agent 的 rag_namespace 一致）
INSERT INTO knowledge_bases (namespace, display_name, description) VALUES
    ('cs_default', '通用知识库', '默认通用客服知识'),
    ('cs_order', '订单知识库', '订单相关业务'),
    ('cs_after_sales', '售后知识库', '售后/投诉相关业务'),
    ('it_default', 'IT 通用知识库', 'IT 服务台自助指南与流程'),
    ('it_network', '网络运维知识库', '办公网络、VPN 与故障排查'),
    ('it_assets', 'IT 资产知识库', '资产领用、保修与使用说明')
ON CONFLICT (namespace) DO NOTHING;

-- ========== 知识库文档归档 ==========
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           VARCHAR(256) NOT NULL,
    source          VARCHAR(512),
    content_type    VARCHAR(128),
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
-- 旧库升级：docx 的 MIME（application/vnd.openxmlformats-officedocument.…）有 71 字符，
-- varchar(64) 会让上传直接 500；放大列宽是幂等操作，新库执行也无副作用。
ALTER TABLE documents ALTER COLUMN content_type TYPE VARCHAR(128);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_namespace ON documents(rag_namespace);
CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind);

-- ========== 工具注册 ==========
-- name 保持全局唯一（首期不做工具名作用域）；scene_id 标记归属，
-- 场景只能绑定本场景或全局工具，校验在应用层完成。
CREATE TABLE tools (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id        UUID REFERENCES scenes(id) ON DELETE CASCADE,
    name            VARCHAR(64) UNIQUE NOT NULL,
    type            VARCHAR(16) NOT NULL CHECK (type IN ('mcp','custom','builtin')),
    schema          JSONB NOT NULL,
    config          JSONB DEFAULT '{}'::jsonb,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tools_scene ON tools(scene_id);

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
CREATE INDEX IF NOT EXISTS idx_audit_user_created ON audit_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);

-- ========== 客诉工单（结构化增改查） ==========
CREATE TABLE IF NOT EXISTS tickets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_no   VARCHAR(32) UNIQUE NOT NULL,
    user_id     UUID,
    session_id  UUID,
    category    VARCHAR(32) NOT NULL DEFAULT 'general',
    urgency     VARCHAR(16) NOT NULL DEFAULT 'medium' CHECK (urgency IN ('low','medium','high')),
    status      VARCHAR(16) NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','escalated','resolved','closed')),
    title       VARCHAR(256),
    content     TEXT NOT NULL DEFAULT '',
    updates     JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tickets_status_created ON tickets(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_user_created ON tickets(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_session ON tickets(session_id);

-- ========== 请求链路（一轮对话一行，明细存 spans） ==========
-- scene_code 用冗余字符串而不是外键：场景归档后链路仍可查；
-- 旧代码未写 scene_code 时落到默认值 'default'，M2 起由 ChatService 显式写入。
CREATE TABLE IF NOT EXISTS request_traces (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id            VARCHAR(64) NOT NULL,       -- 与 structlog / X-Trace-Id 一致
    scene_code          VARCHAR(32) NOT NULL DEFAULT 'default',
    session_id          UUID,
    user_id             UUID,
    visitor_id          UUID,
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
    router_skipped      BOOLEAN NOT NULL DEFAULT FALSE,
    router_fallback     BOOLEAN NOT NULL DEFAULT FALSE,
    router_ms           INT,
    prompt_hash         VARCHAR(16),
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
CREATE INDEX IF NOT EXISTS idx_traces_assistant_msg ON request_traces(assistant_message_id);
CREATE INDEX IF NOT EXISTS idx_traces_scene_created ON request_traces(scene_code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_session_created ON request_traces(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_user_created ON request_traces(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_visitor_created ON request_traces(visitor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_outcome_created ON request_traces(outcome, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_agents ON request_traces USING gin (agent_names);
CREATE INDEX IF NOT EXISTS idx_traces_tools ON request_traces USING gin (tool_names);
CREATE INDEX IF NOT EXISTS idx_traces_mcp ON request_traces USING gin (mcp_server_names);

-- ========== 高频固定话术（硬模板，后台可维护） ==========
-- scene_id NULL=全局（probing 等）；非空=场景覆盖。
-- 同一 key 允许“全局一条 + 每个场景各一条”，运行时按场景优先合并。
CREATE TABLE IF NOT EXISTS canned_scripts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id    UUID REFERENCES scenes(id) ON DELETE CASCADE,
    key         VARCHAR(64) NOT NULL,                    -- 业务可读标识
    title       VARCHAR(128),
    category    VARCHAR(16) NOT NULL DEFAULT 'business', -- identity | capability | probing | business
    patterns    JSONB NOT NULL DEFAULT '[]'::jsonb,      -- 问法主干数组（整句全等）
    keywords    JSONB NOT NULL DEFAULT '[]'::jsonb,      -- 关键词数组（包含即命中）
    reply_text  TEXT NOT NULL DEFAULT '',
    priority    INT NOT NULL DEFAULT 0,                  -- 小者优先
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    is_builtin  BOOLEAN NOT NULL DEFAULT FALSE,          -- 内置项可改文案/停用，不可删
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_canned_scripts_scene_key UNIQUE NULLS NOT DISTINCT (scene_id, key)
);
CREATE INDEX IF NOT EXISTS idx_canned_scripts_scene_enabled_priority
    ON canned_scripts(scene_id, enabled, priority);

-- ========== schema 版本（启动时断言；不匹配请全量重建） ==========
CREATE TABLE IF NOT EXISTS schema_meta (
    key         VARCHAR(64) PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO schema_meta (key, value) VALUES ('version', '2')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
