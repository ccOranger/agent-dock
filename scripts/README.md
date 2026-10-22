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
| 生产环境备份 | `backup.sh` |
| 发布前校验 / 离线包 / 打 tag | `release.py` |

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
- 项目不做增量迁移，结构性变更只改这里 + 全量重建。
- 文件本身不含业务数据。

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

**作用**：全量重建数据库——`DROP` 全部业务表 → 执行 `init_db.sql` → 写入种子。用于开发期重置数据、切换 schema 版本（如 `SCHEMA_VERSION 3 → 4`）。

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

---

## 通用注意事项

1. **工作目录决定读哪份 `.env`**：后端类脚本在 `backend` 目录执行；`init_db.py` 不读 `.env`，需显式提供 `DATABASE_URL`。
2. **不做增量迁移**：`SCHEMA_VERSION` 变化（如新增表 / 列）必须用 `rebuild_and_seed.py` 全量重建，再由启动守卫放行。
3. **密码与凭据**：`.env` 不要提交；重建生成的随机密码只显示一次；演示账号口令仅用于本地演示。
4. **Windows 终端**：PowerShell 用 `;` 连接命令（不支持 `&&`）；跨目录执行时用绝对路径或先 `cd`。
5. **端口冲突**：默认 8000 / 5173 / 5174；异常时先用 `stop_all.ps1 -WhatIf` 看占用，再决定是否清理。
6. **Docker 依赖异常**：Qdrant / Redis 端口连通但自检失败时，先 `docker compose restart qdrant redis`（Windows 版 Docker 端口代理偶发失效）。
