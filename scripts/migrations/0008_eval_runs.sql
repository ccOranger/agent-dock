-- 0008：评测与回归（eval_runs 运行记录）
--
-- 为什么需要：改提示词、换模型、调 RAG 参数之后，没有任何手段证明"没退化"。
-- eval 报告是文件（reports/eval-*.md/json），但交付周期里要回答"上一次跑是多少"，
-- 报告文件在客户环境里会丢，所以运行结论（指标 + 用例集指纹 + 基线指向）落库。
--
-- 只管"记录"：用例集在仓库里（tests/fixtures/eval/*.json），执行与打分在 scripts/eval.py，
-- 后台看板属企业版 eval_pro，本表是它唯一的数据源。
--
-- 幂等：新库可能先跑 init_db.sql（已是新结构）再补迁移记录，全部 IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS eval_runs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- 人工标注：本次跑对应哪个版本/哪次变更，报告里直接引用
    run_tag         VARCHAR(64) NOT NULL DEFAULT '',
    scene_code      VARCHAR(32) NOT NULL DEFAULT 'default',
    -- 被测服务地址与当时的对话模型：换模型即换基线，指标不可混比
    base_url        VARCHAR(255) NOT NULL DEFAULT '',
    model_name      VARCHAR(64) NOT NULL DEFAULT '',
    -- 用例集指纹（内容 sha256 前 16 位）：指纹不同说明用例变了，diff 无意义
    fixture_hash    VARCHAR(16) NOT NULL DEFAULT '',
    case_count      INT         NOT NULL DEFAULT 0,
    passed_count    INT         NOT NULL DEFAULT 0,
    -- 指标口径见 docs/evaluation.md；明细同时写 reports/eval-*.json 便于无库环境对比
    metrics         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- 逐用例结果（截断后的实际应答 + 各检查点通过情况），用于失败复盘
    detail          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    report_path     VARCHAR(255) NOT NULL DEFAULT '',
    -- 本次对比的基线运行（同一 fixture_hash 的上一次）；首次运行为 NULL
    baseline_run_id UUID,
    duration_ms     INT         NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_scene_started
    ON eval_runs(scene_code, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_runs_fixture
    ON eval_runs(fixture_hash, started_at DESC);
