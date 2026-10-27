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
    runtime_config  JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 场景运行参数（空对象=全部继承全局）
    widget_config   JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 标题/欢迎语/主题/位置等
    allowed_origins JSONB NOT NULL DEFAULT '[]'::jsonb,   -- 嵌入允许来源
    publish_key     VARCHAR(64),                          -- 前端嵌入公开标识（非密钥）
    created_by      UUID,                                 -- 创建人（不建外键，与 audit_logs 一致）
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 全库只能有一个默认场景；默认场景由初始化种子写入，只读
CREATE UNIQUE INDEX IF NOT EXISTS uq_scenes_single_default ON scenes(is_default) WHERE is_default;
CREATE UNIQUE INDEX IF NOT EXISTS uq_scenes_publish_key ON scenes(publish_key) WHERE publish_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_scenes_status ON scenes(status);
-- 登录人配额：按 created_by 统计已创建数量
CREATE INDEX IF NOT EXISTS idx_scenes_created_by ON scenes(created_by);

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
    -- 资源创建配额：键缺失/null=不限，0=禁止创建，正数=上限
    quota           JSONB NOT NULL DEFAULT '{}'::jsonb,
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

-- ========== 接入方（服务端 API 调用的身份边界） ==========
-- Key 挂接入方而不是场景：场景是业务维度，接入方是调用方维度。
-- 同一场景可被多个接入方接入，靠接入方隔离访客身份命名空间。
CREATE TABLE integrations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(64) NOT NULL,
    -- 公开标识：嵌入页用 data-integration 传回，用于匿名访客归属与命名空间；
    -- 会出现在宿主页面源码里，不是安全凭证。
    public_code     VARCHAR(32) NOT NULL
                    DEFAULT ('ic_' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 16)),
    description     TEXT,
    status          VARCHAR(16) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','disabled')),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_integrations_name UNIQUE (name),
    CONSTRAINT uq_integrations_public_code UNIQUE (public_code)
);
CREATE INDEX IF NOT EXISTS idx_integrations_status ON integrations(status);

-- 一个接入方可持有多把 Key（轮换过渡期新旧并存）；只存哈希，明文仅签发时回显
CREATE TABLE integration_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id  UUID NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    key_hash        VARCHAR(128) NOT NULL,
    hint            VARCHAR(16) NOT NULL DEFAULT '',        -- 尾号，后台识别用
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_integration_keys_key_hash UNIQUE (key_hash)
);
CREATE INDEX IF NOT EXISTS idx_integration_keys_integration
    ON integration_keys(integration_id);

-- 接入方 ↔ 场景授权：接入方只能调用被授权的场景
CREATE TABLE integration_scenes (
    integration_id  UUID NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    scene_id        UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (integration_id, scene_id)
);
CREATE INDEX IF NOT EXISTS idx_integration_scenes_scene ON integration_scenes(scene_id);

-- ========== 访客（浏览器匿名按场景隔离，服务端接入再按接入方隔离） ==========
CREATE TABLE visitors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id        UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    integration_id  UUID REFERENCES integrations(id) ON DELETE CASCADE,  -- NULL=浏览器匿名
    external_key    VARCHAR(128),                         -- 宿主页面/接入方下传的稳定标识（可选）
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_seen_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- external_key 可空：空表示不做宿主侧归并，允许多个 NULL；
-- 两类命名空间分开建部分唯一索引，避免 NULL 让 ON CONFLICT 失效：
-- 1) 浏览器匿名（integration_id IS NULL）：按场景 + external_key 复用；
-- 2) 服务端接入：按接入方 + 场景 + external_key 复用，不同接入方传相同 id 不会串号。
CREATE UNIQUE INDEX IF NOT EXISTS uq_visitors_anon_scene_external
    ON visitors(scene_id, external_key) WHERE integration_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_visitors_integration_scene_external
    ON visitors(integration_id, scene_id, external_key) WHERE integration_id IS NOT NULL;
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
    created_by      UUID,                                   -- 创建人（代码 Agent 为 NULL）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 场景内同名；scene_id NULL 表示全局共享，NULLS NOT DISTINCT 保证全局名也唯一
    CONSTRAINT uq_agents_scene_name UNIQUE NULLS NOT DISTINCT (scene_id, name),
    -- 代码 Agent 是内核能力，必须全局注册，不能挂在某个场景下
    CONSTRAINT ck_agents_code_global CHECK (source <> 'code' OR scene_id IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_agents_created_by ON agents(created_by);

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
                    CHECK (status IN ('active','closed','handover','human_handling')),
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
    created_by    UUID,                                   -- 创建人（初始化种子为 NULL）
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_created_by ON knowledge_bases(created_by);

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

-- ========== 对话埋点（全量记录，不受链路采样影响） ==========
-- 一行 = 一次聊天请求；ext 是客户端声明的渠道/上下文（如 platform），只用于统计与软策略。
-- tenant_id 预留给多租户：当前恒为 NULL，切换时由凭证解析后注入。
CREATE TABLE IF NOT EXISTS chat_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id        VARCHAR(64) NOT NULL DEFAULT '',
    scene_code      VARCHAR(32) NOT NULL DEFAULT 'default',
    session_id      UUID,
    visitor_id      UUID,
    user_id         UUID,
    integration_id  UUID,
    tenant_id       UUID,
    ext             JSONB NOT NULL DEFAULT '{}'::jsonb,
    message_chars   INT NOT NULL DEFAULT 0,
    total_tokens    INT,                            -- 输入+输出估算合计（供应商 usage 不可得时为估算）
    tokens_estimated BOOLEAN NOT NULL DEFAULT FALSE, -- total_tokens 是否为估算值
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_events_scene_created
    ON chat_events(scene_code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_events_visitor_created
    ON chat_events(visitor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_events_integration_created
    ON chat_events(integration_id, created_at DESC);
-- 身份回填按 trace_id 定位请求行：无索引会随埋点增长退化为全表扫描
CREATE INDEX IF NOT EXISTS idx_chat_events_trace
    ON chat_events(trace_id);
-- 后台“当日全场景用量”按时间范围分组：现有 idx 前导列是场景，扫不了时间范围
CREATE INDEX IF NOT EXISTS idx_chat_events_created_scene
    ON chat_events(created_at DESC, scene_code);

-- ========== 用量日聚合（账单依据，长期保留） ==========
-- chat_events 明细会按保留期清理，本表不跟清；写入由 ChatService 在每轮结束后 upsert。
-- integration_id 用零 UUID 表示“未归属”：唯一约束里 NULL 互不相等，用零值才能稳定 ON CONFLICT。
CREATE TABLE IF NOT EXISTS chat_usage_daily (
    day                 DATE        NOT NULL,
    scene_code          VARCHAR(32) NOT NULL DEFAULT 'default',
    integration_id      UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
    requests            BIGINT      NOT NULL DEFAULT 0,   -- 走完整服务链路的轮次
    tokens              BIGINT      NOT NULL DEFAULT 0,   -- 输入 + 输出估算合计
    estimated_requests  BIGINT      NOT NULL DEFAULT 0,   -- 其中带估算标记的轮次
    handoffs            BIGINT      NOT NULL DEFAULT 0,
    errors              BIGINT      NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (day, scene_code, integration_id)
);
CREATE INDEX IF NOT EXISTS idx_chat_usage_daily_scene
    ON chat_usage_daily(scene_code, day DESC);
CREATE INDEX IF NOT EXISTS idx_chat_usage_daily_integration
    ON chat_usage_daily(integration_id, day DESC);

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
    -- 回答来源：text=下发 reply_text；kb=检索 kb_namespaces 后按固定框架拼接（不走模型）
    answer_source VARCHAR(16) NOT NULL DEFAULT 'text'
                  CHECK (answer_source IN ('text','kb')),
    kb_namespaces JSONB NOT NULL DEFAULT '[]'::jsonb,    -- answer_source=kb 时的检索范围
    priority    INT NOT NULL DEFAULT 0,                  -- 小者优先
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    is_builtin  BOOLEAN NOT NULL DEFAULT FALSE,          -- 内置项可改文案/停用，不可删
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_canned_scripts_scene_key UNIQUE NULLS NOT DISTINCT (scene_id, key)
);
CREATE INDEX IF NOT EXISTS idx_canned_scripts_scene_enabled_priority
    ON canned_scripts(scene_id, enabled, priority);

-- ========== 评测运行记录（scripts/eval.py 写入，后台看板属企业版 eval_pro） ==========
-- 报告本身是文件（reports/eval-*.md/json），但交付周期里要能回答“上一次跑是多少”，
-- fixture_hash 是用例集内容指纹：指纹不同的两次运行指标不可直接比较（用例变了）。
CREATE TABLE IF NOT EXISTS eval_runs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_tag         VARCHAR(64) NOT NULL DEFAULT '',      -- 人工标注：对应哪个版本/哪次变更
    scene_code      VARCHAR(32) NOT NULL DEFAULT 'default',
    base_url        VARCHAR(255) NOT NULL DEFAULT '',      -- 被测服务地址
    model_name      VARCHAR(64) NOT NULL DEFAULT '',       -- 当时的对话模型：换模型即换基线
    fixture_hash    VARCHAR(16) NOT NULL DEFAULT '',       -- 用例集指纹（sha256 前 16 位）
    case_count      INT         NOT NULL DEFAULT 0,
    passed_count    INT         NOT NULL DEFAULT 0,
    metrics         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    detail          JSONB       NOT NULL DEFAULT '[]'::jsonb,  -- 逐用例结果（应答截断）
    report_path     VARCHAR(255) NOT NULL DEFAULT '',
    baseline_run_id UUID,                                  -- 本次对比的基线运行；首次为 NULL
    duration_ms     INT         NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_scene_started
    ON eval_runs(scene_code, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_runs_fixture
    ON eval_runs(fixture_hash, started_at DESC);

-- ========== schema 版本（启动时断言；不匹配请执行 python -m app.migrate） ==========
CREATE TABLE IF NOT EXISTS schema_meta (
    key         VARCHAR(64) PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 只在缺失时写入：存量库的版本由迁移推进，重复执行本脚本不得把版本降回去
INSERT INTO schema_meta (key, value) VALUES ('version', '9')
ON CONFLICT (key) DO NOTHING;
