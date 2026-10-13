-- 管理后台增量迁移（针对已部署库，幂等可重复执行）
-- 对应 init_db.sql 的新增：knowledge_bases 表 + documents.kind 列

-- ========== 知识库命名空间 ==========
CREATE TABLE IF NOT EXISTS knowledge_bases (
    namespace     VARCHAR(64) PRIMARY KEY,
    display_name  VARCHAR(128) NOT NULL,
    description   TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========== documents 增加 kind 列 ==========
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'upload';

-- 若旧库无该 CHECK 约束，补充（约束名固定，重复执行前需确保仅一个）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'documents_kind_check' AND conrelid = 'documents'::regclass
    ) THEN
        ALTER TABLE documents
            ADD CONSTRAINT documents_kind_check CHECK (kind IN ('upload','manual'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind);

-- ========== 种子（与现有 agent rag_namespace 一致） ==========
INSERT INTO knowledge_bases (namespace, display_name, description) VALUES
    ('cs_default', '通用知识库', '默认通用客服知识'),
    ('cs_order', '订单知识库', '订单相关业务'),
    ('cs_after_sales', '售后知识库', '售后/投诉相关业务')
ON CONFLICT (namespace) DO NOTHING;
