# smart-cs — 智能客服系统

> 基于 **HelloAgents + FastAPI + Vue 3** 的多 Agent 智能客服系统。
> 完整开发规范见 [docs/spec.md](docs/spec.md)（Spec v2.0，与代码实现同步）；各已完成功能的详解见 [docs/updates/](docs/updates/README.md)。

## 架构一览

```
用户消息 → RouterAgent（意图路由，轻量模型）
             ├─ knowledge_qa    KnowledgeAgent（MemoryTool + RAGTool）
             ├─ order_service   OrderAgent（MemoryTool + 订单/物流工具 + MCP）
             └─ complaint_handler ComplaintAgent（MemoryTool + 工单/升级工具）
```

- 每个业务 = 一个独立 Python 类（继承 `BaseBusinessAgent`）+ 独立 Prompt + 独立工具集
- 新增业务：新建类文件 + 注册表加一行，路由 Prompt 自动感知，零改路由代码
- **动态 Agent**：除代码托管 Agent 外，可在管理后台基于数据库配置（system_prompt / 模型 / RAG namespace / MCP 工具）动态创建，注册与路由实时生效（代码 Agent 与动态 Agent 混合注册）
- 记忆一律走 HelloAgents `MemoryTool`（四层），知识库一律走 `RAGTool`（多 namespace）
- 对话接口 SSE 流式输出（session / route / text / tool_call / tool_result / handover / done / error）
- **管理后台**：独立 `admin-frontend` 工程，提供 Agent 管理、知识库命名空间管理、跨客户工单（咨询）查看/CSV 导出、MCP Servers、记忆时序等能力

## 目录结构

```
backend/         FastAPI + HelloAgents（app/{api,core,agents,tools,models,schemas,services,utils}）
frontend/        Vue 3 客户端（客户对话端，src/{api,stores,router,views,components,types}）
admin-frontend/  Vue 3 管理后台（Agent/知识库/工单/MCP/记忆/登录，独立工程）
backend/data/    运行时数据：文档归档 doc_archives/、知识库 knowledge_bases/（RAG_KB_ROOT 相对 backend 工作目录；已 .gitignore 排除，勿入库）
scripts/         init_db.sql（全量 schema）/ init_db.sh（本机一键建库）/ seed_data.py（种子定义，幂等）/ rebuild_and_seed.py（开发阶段全量重建+灌种子）/ seed_memory_via_chat.py（模拟对话造记忆测试数据）
docs/            spec.md（规范 v2.0）/ api.md（接口速查）/ runbook.md（运维手册）/ updates/（U01~U09 各功能详解文档）
```

## 快速开始

### 前置要求

- Python >= 3.11, < 3.13（本仓库 `backend/.venv` 由 **uv** 管理）
- Node >= 18.18（包管理器 npm / pnpm 均可，本文以 **npm** 为例）
- 外部依赖服务（可本地或云托管，连接串统一在 `backend/.env` 配置）：
  - **PostgreSQL**（14+ 即可；建表脚本已适配为**无需** `uuid-ossp` / `pgvector` 扩展，默认 UUID 用内置 `gen_random_uuid()`）
  - **Redis**（工作记忆 / 缓存）
  - **Qdrant**（向量库：HelloAgents 的记忆与 RAG 底层存储，RAG 向量**不落** PostgreSQL）
  - **LLM / Embedding 服务**（OpenAI 兼容端点即可，如 DashScope）
- `hello-agents==0.2.9`（配套约束：`qdrant-client<1.10`、`bcrypt==4.0.1`，已钉在 `requirements.txt`）

### 1. 数据库

```bash
bash scripts/init_db.sh          # 或手动执行 scripts/init_db.sql
```

> 若用云托管库（非本机），推荐直接用下文的 `rebuild_and_seed.py`（单往返 simple query，规避连接池代理断连）；本机库可用 `psql`/客户端执行 `scripts/init_db.sql`（已去除 `uuid-ossp`/`vector` 依赖），或交由 `seed_data.py` 的 `create_all` 自动建表。

> **开发阶段的 schema 重置**：本项目尚处开发阶段，schema 频繁变动（如 `users` 已拆分为 `staff_users` / `customer_users`，相关外键重指向）。改动 ORM 模型后，无需手写增量迁移，直接对目标库**全量重建 + 灌种子**（⚠️ 会清空现有业务数据，仅限开发）：
>
> ```powershell
> cd backend
> .\.venv\Scripts\python.exe ..\scripts\rebuild_and_seed.py   # DROP 旧表(含遗留 users) → 建表 → seed；单往返 simple query 适配云库代理
> ```

### 2. 后端

```powershell
cd backend
# 建虚拟环境并安装依赖（本仓库用 uv；也可 python -m venv .venv 后用 pip）
uv venv --python 3.12
uv pip install -r requirements.txt --python .venv\Scripts\python.exe

copy .env.example .env           # 填入 DATABASE_URL / REDIS_URL / QDRANT_* / LLM_* / EMBED_* 等
python ..\scripts\seed_data.py   # 建表 + 初始用户/Agent/工具/示例文档（幂等，可重复执行）
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

> `seed_data.py` 需在 `backend` 目录下运行（`.env` 按当前工作目录加载）；它会 `create_all` 建表，故即使跳过 `init_db.sql` 也能起来。

- Swagger 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

### 3. 前端

```powershell
cd frontend
npm install                      # 或 pnpm install
npm run dev                      # http://localhost:5173（/api 已代理到后端 8000）
```

> 请用 **`http://localhost:5173`** 访问，不要用 `127.0.0.1`——Vite dev 可能只监听 IPv6 `::1`，用 `127.0.0.1` 会连不上。

默认账号（seed 注入，生产务必改密）：

| 用户名 | 密码 | 角色 |
|--------|------|------|
| admin | Admin@123 | admin |
| alice | Alice@123 | customer |
| service01 | Agent@123 | agent |

### 4. 管理后台（admin-frontend）

```powershell
cd admin-frontend
npm install                      # 或 pnpm install
npm run dev                      # http://localhost:5174（/api 已代理到后端 8000）
```

> 管理后台为独立工程，端口 **5174**（与客户前端 5173 分离，共用同一后端）。使用 `admin` 角色账号登录。
> 功能：Agent 列表与动态 Agent 创建/启停、知识库命名空间与文档管理、跨客户工单查看与 CSV 导出、MCP Servers 配置、记忆时序。

### 5. 测试

```bash
cd backend
pytest                # 纯逻辑单测（无 hello-agents 时自动桩化）
pytest --cov=app      # 覆盖率
cd ../frontend
npm test              # vitest
```

## 核心约束（摘自 Spec 2.5）

- 所有 Agent / 记忆 / RAG 能力必须通过 HelloAgents 框架 API 调用
- 所有业务 Agent 必须继承 `BaseBusinessAgent`
- 工具一律通过 `ToolRegistry.register_tool()` 注册
- 禁止绕过 RAGTool 直接操作底层向量库

## 常见问题（本环境实测）

| 现象 | 原因 / 处理 |
|------|-------------|
| 建表报扩展不存在（uuid-ossp / vector） | 服务器未装且非超管；`init_db.sql` 已改用内置 `gen_random_uuid()` 且不依赖 pgvector |
| `hash_password` 报 `password cannot be longer than 72 bytes` | passlib 1.7.4 与 bcrypt 5.x 不兼容；须 `bcrypt==4.0.1` |
| `/chat/stream` 报 `Future attached to a different loop` | 跨事件循环访问 asyncpg 连接池；MCP 配置已在主 loop 预取 |
| 前端登录弹窗 `Cannot read properties of undefined (reading 'data')` | `unwrap` 未 `await` Promise（已修复）；前端数据层依赖此函数 |
| 控制台 `favicon.ico 404` | 无害；`index.html` 已改为引用 `/vite.svg` |
| 启动报缺列/缺表 / 外键不一致（模型改字段后） | schema 与库不一致；开发阶段直接执行 `scripts/rebuild_and_seed.py` 全量重建（会清空数据，重建含 `users` 拆分后的 `staff_users`/`customer_users`） |

## 文档

- [开发规范 Spec v2.0](docs/spec.md)
- [功能更新详解（U01~U09）](docs/updates/README.md)
- [API 速查](docs/api.md)
- [运维手册](docs/runbook.md)
