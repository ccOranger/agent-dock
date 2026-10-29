# scripts 脚本说明

本目录存放数据库初始化 / 种子 / 校验、开发环境启停、备份与发布脚本。

## 快速选择

| 我想做什么 | 用哪个 |
|---|---|
| 一键启动本地开发环境（后端 + 两个前端） | `start_all.ps1` / `start_all.bat` |
| 关停开发环境 | `stop_all.ps1` |
| 空库建表（容器首启 / 幂等补对象） | `init_db.py`（执行 `init_db.sql`） |
| 开发期重置数据库 / 切换 schema 版本 | `rebuild_and_seed.py` |
| 只补种子数据 | `seed_data.py` |
| 重建后校验结构、约束、种子与版本 | `verify_schema.py` |
| 清理过期请求链路 | `cleanup_traces.py` |
| 跑评测与回归（改提示词/换模型/调 RAG 后的发布闸门） | `eval.py` |
| 生产环境备份 | `backup.sh` |
| 发布前校验 / 离线包 / 打 tag | `release.py` |
| 生成 / 校验第三方依赖许可证清单 | `license_report.py` |

> 后端相关的 Python 脚本（`rebuild_and_seed.py`、`seed_data.py`、`verify_schema.py`、`cleanup_traces.py`）**统一在 `backend` 目录下执行**，因为它们通过 `Settings(env_file=".env")` 读取配置，`.env` 按工作目录解析。`init_db.py` 是例外：它直接读进程环境变量 `DATABASE_URL`。

---

## 开发环境启停

### start_all.ps1

**作用**：一键拉起开发环境，每个服务一个独立 PowerShell 窗口：

- 后端 uvicorn（默认 8000，工作目录 `backend`，`--reload-dir app --reload-dir mcp_servers`）
- 客户前端 Vite（5173）
- 管理后台 Vite（5174）

**执行步骤**（仓库根目录）：

```powershell
.\scripts\start_all.ps1              # 直接启动
.\scripts\start_all.ps1 -Install     # 缺 venv / node_modules 时先装依赖
.\scripts\start_all.ps1 -BackendPort 8001
```

**注意事项**：

- 后端必须从 `backend` 目录启动才能读到 `backend/.env`；不要从仓库根直接跑 `uvicorn`（根 `.env` 指向另一套库）。
- 前端请用 `http://localhost:5173` / `http://localhost:5174` 访问，不要用 `127.0.0.1`（Vite 默认只监听 IPv6）。
- 关闭某个窗口即停止对应服务；也可用 `stop_all.ps1` 统一清理。
- 改 `-BackendPort` 后，前端 Vite 代理仍固定指向 8000，一般不需要改后端端口。
- 临时脚本放在 `backend` 根目录不会触发后端热重载（已用 `--reload-dir` 收窄监听范围）。

### start_all.bat

**作用**：Windows 双击入口，把参数原样转发给 `start_all.ps1`。

**执行步骤**：

```bat
start_all.bat
start_all.bat -Install
```

**注意事项**：内部已带 `-ExecutionPolicy Bypass`，无需手动改执行策略。

### stop_all.ps1

**作用**：关停 `start_all.ps1` 启动的环境。先按窗口标题 `smart-cs*` 结束进程树，再按端口 8000/5173/5174 兜底清理孤儿进程。

**执行步骤**：

```powershell
.\scripts\stop_all.ps1
.\scripts\stop_all.ps1 -WhatIf                       # 只预演，不实际结束
.\scripts\stop_all.ps1 -Ports 8001                   # 只按指定端口清理
.\scripts\stop_all.ps1 -TitlePattern 'smart-cs backend*'  # 只关后端
```

**注意事项**：`taskkill /T /F` 会连同子进程一起结束；执行前可用 `-WhatIf` 确认命中的进程。

---

## 数据库初始化与种子

### init_db.sql

**作用**：全项目唯一的 DDL 源（表、约束、索引、部分唯一索引、`schema_meta.version`），只保留最新结构。

**执行步骤**：不直接执行，由以下入口调用：

- 容器首启：`docker/backend-entrypoint.sh` 自动执行 `init_db.py`
- 本地幂等建表：`init_db.py`
- 全量重建：`rebuild_and_seed.py`

**注意事项**：

- 末尾写入的 `schema_meta.version` 必须与 `backend/app/core/schema.py` 的 `SCHEMA_VERSION` 一致，否则后端启动会被守卫拦下。
- 该写入用 `ON CONFLICT DO NOTHING`：只在版本行缺失时生效，存量库的版本由迁移推进，重复执行本脚本不会把版本降回去。
- 结构性变更：改这里（空库快路径）+ 新增 `scripts/migrations/NNNN_*.sql`（存量库升级），并同步 `SCHEMA_VERSION`。
- 文件本身不含业务数据。

### migrations/（增量迁移）

**作用**：存量库的 schema 升级包，命名 `NNNN_slug.sql`，只增不改。

**执行方式**（必须在 `backend` 目录，`DATABASE_URL` 指向目标库）：

```bash
python -m app.migrate --check      # 当前版本 + 待执行列表（有待执行时退出码 1）
python -m app.migrate --dry-run    # 事务内试跑后回滚
python -m app.migrate --backup     # 有待执行时先 pg_dump
python -m app.migrate              # 正式执行
```

**注意事项**：

- 空库不需要 baseline 文件：`migrate` 检测到 `schema_meta` 不存在时会直接执行 `init_db.sql` 建到最新结构。
- 每个增量必须幂等（`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`），因为可能在“已经是新结构”的库上重放。
- 历史文件不可修改：`schema_migrations.checksum` 会校验，checksum 按 LF 归一化以保证跨平台一致。
- 新增迁移时必须同步 `init_db.sql` 的结构与末尾版本号、`schema.py` 的 `SCHEMA_VERSION`。
- `--backup` 依赖 `pg_dump`：backend 镜像里没有 postgresql 客户端，找不到时会**跳过备份并提示**（不中止迁移）。生产升级请用宿主机的 `scripts/backup.sh`，或本机安装客户端后单独跑 `--backup`。
- 详细规范见 [migrations/README.md](./migrations/README.md)。

### init_db.py

**作用**：在一个事务里逐条执行 `init_db.sql`，用 savepoint 跳过“对象已存在”（SQLSTATE 42P07/42710/42P06），其余错误整体回滚。适合容器首启和空库建表，幂等可重复运行。

**执行步骤**：

Docker（entrypoint 已自动处理，也可手动）：

```bash
docker compose exec backend python ../scripts/init_db.py
```

本地（`init_db.py` 不读 `.env`，必须先设环境变量）：

```powershell
cd D:\project\smart-cs\backend
$env:DATABASE_URL = "postgresql+asyncpg://<user>:<pass>@<host>:5432/<db>"
.\.venv\Scripts\python.exe ..\scripts\init_db.py
```

**注意事项**：

- 不删除数据、不灌种子、不做结构性迁移（只含少量幂等列宽修正）。
- 已有库升级 schema 大版本时不要用它“凑合”，应使用 `rebuild_and_seed.py`。

### init_db.sh

**作用**：Linux 裸机 PostgreSQL 13+ 场景下创建用户/数据库并执行 `init_db.sql`（不依赖 pgvector / uuid-ossp）。

**执行步骤**（仓库根目录）：

```bash
bash scripts/init_db.sh
```

**注意事项**：

- 需要 `sudo -u postgres` 权限，仅适用于裸机；Docker 部署不需要。
- 默认 `cs_user` / `cs_db` / 密码 `cs_pass`，可用 `DB_USER`、`DB_NAME` 覆盖；生产环境请勿使用默认密码。

### rebuild_and_seed.py

**作用**：全量重建数据库——`DROP` 全部业务表 → 执行 `init_db.sql` → 写入种子。**仅用于开发/演示**：重置数据、切换 `SCHEMA_VERSION`。

`APP_ENV=production` 时默认拒绝执行（避免误删生产数据），确需重建必须追加 `--force`；生产升级一律用 `python -m app.migrate`。

**执行步骤**（必须在 `backend` 目录）：

非演示模式（默认，仅创建管理员，随机密码只打印一次）：

```powershell
cd D:\project\smart-cs\backend
.\.venv\Scripts\python.exe ..\scripts\rebuild_and_seed.py
```

演示模式（写入固定演示账号与示例数据，含 `customer_service` 客服场景与默认接入方 `ic_default`（授权全部场景），Qdrant 可用时补文档向量）：

```powershell
cd D:\project\smart-cs\backend
$env:SEED_DEMO = "true"
.\.venv\Scripts\python.exe ..\scripts\rebuild_and_seed.py
```

固定管理员密码（可选）：

```powershell
$env:SEED_ADMIN_PASSWORD = "你的密码"
```

**注意事项**：

- **会清空全部业务数据**，禁止直接对生产库执行。
- 必须在 `backend` 目录执行；在仓库根执行会读到根 `.env`（可能指向云库），重建到错误的库。
- 非演示模式的随机密码由 `secrets.token_urlsafe(12)` 生成，仅终端打印一次。
- 演示模式固定账号见 `seed_data.py` 的 `DEMO_STAFF` / `DEMO_CUSTOMERS`；默认接入方公开标识见 `DEMO_INTEGRATION_PUBLIC_CODE`（单租户降级：嵌入无需传 `data-integration`）。
- 重建完成后立即执行 `verify_schema.py` 校验。

### seed_data.py

**作用**：只写种子、不建表（后台人员、客户、工具、Agent、默认场景、固定话术、演示数据），可选把演示文档写入 Qdrant。

**执行步骤**（`backend` 目录，先执行过 `init_db.py` 或 `rebuild_and_seed.py`）：

```powershell
.\.venv\Scripts\python.exe ..\scripts\seed_data.py
.\.venv\Scripts\python.exe ..\scripts\seed_data.py --skip-docs   # 不碰 Qdrant
.\.venv\Scripts\python.exe ..\scripts\seed_data.py --force-docs  # 已有向量也重灌
```

**注意事项**：

- 默认非演示模式：只创建管理员；`SEED_DEMO=true` 才写演示账号与示例文档。
- 管理员已存在时不会重置密码，随机密码只在“本次新建”时打印。
- `--skip-docs` 适合 Qdrant 不可用的环境；`--force-docs` 用于向量数据损坏后重灌。
- 容器首启时 entrypoint 会自动执行一次。

### verify_schema.py

**作用**：校验 `init_db.sql` + 种子的产出，分四层：

1. 结构：表、列、NOT NULL、唯一约束 / CHECK / 部分索引；
2. 行为：该失败的写入确实失败、该放行的写入确实成功（事务内执行后统一 rollback）；
3. 种子：默认场景、默认 Agent、绑定关系、默认知识库；
4. 版本：`schema_meta.version` 与 `backend/app/core/schema.py` 一致。

**执行步骤**（`backend` 目录）：

```powershell
.\.venv\Scripts\python.exe ..\scripts\verify_schema.py
```

**注意事项**：

- 只读校验：不建表、不灌种子、不改数据。
- 任何断言失败会列出缺失的表 / 约束 / 索引或版本差异。
- 检查项覆盖访问命名空间的部分唯一索引行为、不同接入方相同 `external_key` 互不影响等用例。

### cleanup_traces.py

**作用**：清理超过保留期的 `request_traces` 链路记录。

**执行步骤**（`backend` 目录）：

```powershell
.\.venv\Scripts\python.exe ..\scripts\cleanup_traces.py             # 按 TRACE_RETENTION_DAYS
.\.venv\Scripts\python.exe ..\scripts\cleanup_traces.py --days 7    # 只保留 7 天
.\.venv\Scripts\python.exe ..\scripts\cleanup_traces.py --dry-run   # 只统计不删除
```

**注意事项**：

- 默认保留天数取 `.env` 的 `TRACE_RETENTION_DAYS`，`--days` 最小按 1 天处理。
- 建议先 `--dry-run` 确认待删数量。
- 只删 `request_traces`，不影响会话、消息与记忆。

---

## 运维与发布

### backup.sh

**作用**：备份持久化数据到带时间戳的目录，包含：

- `postgres.dump`（pg_dump 自定义格式）
- `qdrant-*.snapshot`（Qdrant 全量快照）
- `backenddata/`（知识库、文档归档）
- `memorydata/`（hello-agents 记忆 SQLite）
- `redis.rdb`（Redis RDB 快照）
- `manifest.txt`（备份清单）

**执行步骤**（仓库根目录，要求 Compose 服务处于运行状态）：

```bash
bash scripts/backup.sh
COMPOSE_FILE="docker-compose.yml:docker-compose.prod.yml" bash scripts/backup.sh
```

可选环境变量：

- `BACKUP_ROOT`：备份根目录，默认 `./backups`
- `BACKUP_RETAIN_DAYS`：保留天数，默认 `7`，`0` 表示不清理

**注意事项**：

- 读取仓库根 `.env` 的 `POSTGRES_*`、`REDIS_PASSWORD`、`QDRANT_URL`。
- Qdrant 快照通过 `backend` 容器调用 API 生成，无需向宿主机暴露 6333。
- 备份内含完整数据库与向量数据，注意存放位置与权限。

### release.py

**作用**：发布流程工具（纯标准库，跨平台），三个子命令：

- `check`：版本一致性（CHANGELOG / 各 package.json / pyproject / main.py / shared）、必备文件、仓库卫生，默认还会跑 ruff + pytest + 前端构建；
- `bundle`：`docker save` 导出离线镜像包，附校验和、加载脚本与离线说明；
- `tag`：创建 annotated tag，默认只提示（dry-run），加 `--push` 才推送。

**执行步骤**（仓库根目录）：

```powershell
python scripts/release.py check
python scripts/release.py check --skip-tests --skip-build
python scripts/release.py check --allow-dirty
python scripts/release.py bundle --dry-run
python scripts/release.py bundle
python scripts/release.py tag
python scripts/release.py tag --push
```

**注意事项**：

- `check` 会校验 `CHANGELOG.md` 顶部版本与 `package.json`、`pyproject.toml`、`app/main.py`、`packages/shared` 完全一致。
- `tag` 只有显式 `--push` 才会推送到 origin；`--force` 会覆盖已存在的同名 tag，慎用。
- `bundle` 需要本机 Docker；`--no-build` 只导出已有镜像，`--out` 可改输出目录（默认 `dist/`）。

### license_report.py

**作用**：生成 `docs/third-party-licenses.md`（第三方依赖许可证清单），并可在发布前做白名单校验。
Python 侧枚举 `backend/.venv` 里实际安装的发行版（含传递依赖，排除 `node_modules` 里被误认的 JS 包），
Node 侧读 `package-lock.json` 的 `packages`（自带 `license` 与 `dev` 标记，因此能区分是否随镜像分发）。

**执行步骤**（仓库根目录，必须用后端虚拟环境跑，否则扫不到 Python 依赖）：

```powershell
.\backend\.venv\Scripts\python.exe scripts\license_report.py           # 重新生成清单
.\backend\.venv\Scripts\python.exe scripts\license_report.py --check  # 只校验，有未确认项时退出码 1
```

**注意事项**：

- 白名单是 MIT / BSD / Apache-2.0 / ISC / PSF / MPL-2.0 / 0BSD / Unlicense 等宽松许可；**白名单之外一律拦下**，不默认“大概是 MIT”。
- 输出文件勿手改，依赖变动后重跑即可；CI 的 `audit` job 会跑 `--check`（失败只告警，见 `.github/workflows/ci.yml`）。
- 待确认项的处置结论记在 `docs/open-source-release.md` 的 B4，目前唯一待确认项是 `hello-agents`（CC BY-NC-SA 4.0）。

### eval.py

**作用**：评测与回归执行器。跑 `backend/tests/fixtures/eval/<scene>.json` 里的用例，**走真实 HTTP 链路**
（`POST /api/v1/chat/stream`）而不是 import Agent，以便覆盖鉴权 / 限流 / 场景路由 / 固定话术 / 链路回读；
RAG 与 token 按 `trace_id` 从 `request_traces` / `chat_events` 回读（服务端实测，不是模型自述）。
产出 `reports/eval-<timestamp>.md` + 同名 `.json`，并写一行 `eval_runs`。

**执行步骤**（`backend` 目录）：

```powershell
.\.venv\Scripts\python.exe ..\scripts\eval.py --dry-run                    # 只校验并列出用例，不发请求
.\.venv\Scripts\python.exe ..\scripts\eval.py --scene customer_service      # 建基线
.\.venv\Scripts\python.exe ..\scripts\eval.py --scene customer_service --baseline latest
.\.venv\Scripts\python.exe ..\scripts\eval.py --scene default --customer alice --password Alice@123 --concurrency 2
.\.venv\Scripts\python.exe ..\scripts\eval.py --tag 知识 --limit 5 --no-db   # 子集冒烟，不写库
```

**退出码**：`0` 全通过 / `1` 有失败（或 `--dry-run` 用例集不合法）/ **`2` 相对基线退化** / `3` 全部用例都没跑通。

**注意事项**：

- 需要先有服务在跑且 `/health` 可达（它挂在根路径，不带 `/api/v1`）；模型有速率限制，`--concurrency` 别无脑拉高。
- `--baseline latest` 取的是 `eval_runs` 里**同用例集指纹 + 同场景**的最近一次；也可传 sidecar `.json` 路径做离线对比。
- 用例集进 Git、评审后才可改；**不得改期望迁就结果**（口径修正要在 `note` 里写清依据），详见 [docs/evaluation.md](../docs/evaluation.md)。
- 不进 CI：真实链路要打模型；打分/聚合/diff/报告渲染的回归由 `backend/tests/test_eval_runner.py`（全部 mock）覆盖。
- `reports/` 已在 `.gitignore`：报告随交付单归档，跨时间对照看 `eval_runs`。

---

## 通用注意事项

1. **工作目录决定读哪份 `.env`**：后端类脚本在 `backend` 目录执行；`init_db.py` 不读 `.env`，需显式提供 `DATABASE_URL`。
2. **schema 变更走迁移**：新增表/列时写 `scripts/migrations/NNNN_*.sql`，并同步 `init_db.sql` 与 `SCHEMA_VERSION`，用 `python -m app.migrate` 升级；`rebuild_and_seed.py` 仅用于开发/演示，生产环境拒绝执行。
3. **密码与凭据**：`.env` 不要提交；重建生成的随机密码只显示一次；演示账号口令仅用于本地演示。
4. **Windows 终端**：PowerShell 用 `;` 连接命令（不支持 `&&`）；跨目录执行时用绝对路径或先 `cd`。
5. **端口冲突**：默认 8000 / 5173 / 5174；异常时先用 `stop_all.ps1 -WhatIf` 看占用，再决定是否清理。
6. **Docker 依赖异常**：Qdrant / Redis 端口连通但自检失败时，先 `docker compose restart qdrant redis`（Windows 版 Docker 端口代理偶发失效）。
7. **评测不在 CI 里跑**：`eval.py` 要打真实模型，不适合做持续集成；发布前手动跑一次带 `--baseline latest` 的运行，退出码 2 即回滚（见 runbook 6.1）。
