-- 0007：用量计量（token 落到 chat_events + 按日聚合）
--
-- 为什么需要：chat_events.total_tokens 自建表起就是预留列，恒为 NULL；quota_service 统计的
-- 是"建了几个资源"，ratelimit 统计的是"分钟内几次请求"，两者都不是可对账的用量事实。
-- 本迁移只产出"用量事实"，不做套餐/账单/支付。
--
-- 幂等：客户可能先跑新版 init_db.sql（已是新结构）再补迁移记录，本文件必须能在
-- 新结构上重放，因此全部使用 IF NOT EXISTS。

-- 估算标记：当前 hello_agents 的 LLM 封装不回传 usage，total_tokens 是估算值；
-- 将来接入供应商 usage 时置 FALSE，账目里才能区分"真实用量"与"估算用量"。
ALTER TABLE chat_events ADD COLUMN IF NOT EXISTS tokens_estimated BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS chat_usage_daily (
    day                 DATE        NOT NULL,
    scene_code          VARCHAR(32) NOT NULL DEFAULT 'default',
    -- 零 UUID 表示未归属（浏览器匿名 / 未带接入方）：PostgreSQL 唯一约束里 NULL 互不相等，
    -- 只有落到具体的零值上，ON CONFLICT 才能稳定命中同一行。
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
