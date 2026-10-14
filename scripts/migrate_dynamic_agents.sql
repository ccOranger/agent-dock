-- 动态 Agent 增量迁移（针对已部署库，幂等可重复执行）
-- 对应 init_db.sql 的新增：agents 表扩展动态 Agent 相关列
-- 执行：在目标库上 psql ... -f scripts/migrate_dynamic_agents.sql

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'code';

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS system_prompt TEXT;

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS llm_model VARCHAR(128) NOT NULL DEFAULT '';

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS rag_namespace VARCHAR(64);

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS search_all_knowledge_bases BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS mcp_tool_names JSONB NOT NULL DEFAULT '[]'::jsonb;

-- 现存行均为代码托管 Agent，回填 source='code'（默认值已覆盖，此处保险起见显式声明）
UPDATE agents SET source = 'code' WHERE source IS NULL OR source = '';
