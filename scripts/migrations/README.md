# 增量迁移目录

存量库（已有真实数据）的 schema 升级包。**空库不走这里**：`scripts/init_db.sql`
直接建到最新结构，由 `python -m app.migrate` 自动执行。

## 命名

```
NNNN_slug.sql      # 四位编号 + 下划线 + 小写蛇形名，例如 0007_usage_and_eval.sql
```

- 编号唯一、升序执行，**不允许跳号复用**；
- 文件名即版本号：执行后 `schema_meta.version` 被写成 `7`（去掉前导零）；
- 一经发布**永不修改**：`schema_migrations.checksum` 会校验，改动会直接报错中止。
  需要修正时新增下一个编号。

## 必须幂等

迁移可能在"已经是新结构"的库上重放（例如客户先跑了新版 `init_db.sql`，
补迁移记录后又执行增量），因此每条语句都要能重复执行：

- 建表/索引：`CREATE TABLE IF NOT EXISTS`、`CREATE INDEX IF NOT EXISTS`
- 加列：`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
- 建约束：`ALTER TABLE ... ADD CONSTRAINT` 不支持 `IF NOT EXISTS`，
  用 `DO $$ ... $$` 判存在，或改用唯一索引（`CREATE UNIQUE INDEX IF NOT EXISTS`）
- 写数据：`INSERT ... ON CONFLICT DO NOTHING`

这样 "最新结构 + 全部增量" 与 "最新结构" 等价，parity 测试才有意义。

## 新增迁移的步骤

1. 只改增量文件，不动历史文件；
2. 同步把同样的变更写进 `scripts/init_db.sql`（空库快路径必须是最新结构），
   并把文件末尾 `schema_meta` 的 `'version'` 改成新编号；
3. 同步 `backend/app/core/schema.py` 的 `SCHEMA_VERSION`；
4. 本地验证：
   ```bash
   cd backend
   python -m app.migrate --check      # 列出待执行
   python -m app.migrate --dry-run    # 事务内试跑后回滚
   python -m app.migrate --backup     # 有待执行时先 pg_dump
   python -m app.migrate              # 正式执行
   ```
5. 跑 `pytest tests/test_migrations.py`（校验命名、版本一致、历史不被修改）。

## 为什么没有 baseline 文件

baseline 直接复用 `scripts/init_db.sql`，不另存一份副本：

- 只有一份 DDL，不存在"基线副本与最新结构漂移"的维护负担；
- 空库建到最新版，不需要重放历史 DDL；
- 代价是**新库无法验证历史迁移链**，因此用两条约束兜住：增量必须幂等
  （可在最新结构上重放）、以及测试断言 `SCHEMA_VERSION == init_db.sql 版本`。

## 与 `rebuild_and_seed.py` 的关系

`rebuild_and_seed.py` 是开发/演示用的全量重建（DROP 全部业务表），
`APP_ENV=production` 时默认拒绝执行，必须 `--force`。生产升级一律走本目录的迁移。
