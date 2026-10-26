# 智能客服系统 — 可执行开发规范（Spec v2.0）

> **文档版本**：v2.0
> **状态**：与代码实现同步（Implementation-synced）
> **核心变更**：
> - Agent / MemoryTool / RAGTool 全部基于 **HelloAgents** 框架原生 API
> - 多 Agent 采用 **业务独立开发 + 路由 Agent 分发** 架构
> - **v2.0**：随迭代落地对 v1.x 设计做就地校正 —— 依赖版本、Schema（人员/客户分表、tickets、request_traces、knowledge_bases、动态 Agent）、异步工具执行、多意图路由、链路追踪、知识库管理、双入口鉴权等
> **适用框架**：HelloAgents + FastAPI + Vue 3
> **目标读者**：后端工程师、前端工程师、测试工程师

---

## 0. 实现现状总览（v2.0）

> 本 Spec 是**可执行开发规范（SSOT）**；下文各章已就地校正为当前代码的真实实现。每一处相对 v1.x 的**增量特性**另有独立详解文档（含背景、设计、关键实现、契约、配置、验证与边界），收录于 [`docs/updates/`](updates/README.md)。二者冲突时，以本目录与代码为准。

| 更新 | 主题 | 影响章节 | 详解 |
|------|------|----------|------|
| U01 | HelloAgents 0.2.9 集成与依赖适配（`qdrant-client<1.10`、`bcrypt==4.0.1`、`BaseTool` 适配层） | §2、§6 | [↗](updates/01-helloagents-integration.md) |
| U02 | 多意图路由与追问续接 | §7、§10 | [↗](updates/02-multi-intent-routing.md) |
| U03 | 动态 Agent 混合注册与路由实时化 | §4、§7 | [↗](updates/03-dynamic-agents.md) |
| U04 | 请求链路追踪（request_traces） | §4、§14 | [↗](updates/04-request-tracing.md) |
| U05 | stdio 型本地 MCP 演示服务与固定数据 | §6、§12 | [↗](updates/05-stdio-mcp-demo.md) |
| U06 | 工单独立表（tickets）与跨轮记忆 | §4、§8 | [↗](updates/06-tickets-cross-round-memory.md) |
| U07 | 人员/客户分表（staff_users / customer_users）与双入口鉴权 | §4、§12 | [↗](updates/07-staff-customer-auth-split.md) |
| U08 | 知识库管理增强（namespace CRUD / 归档下载 / 手工 FAQ / 分块浏览 / 高级检索） | §4、§9 | [↗](updates/08-knowledge-base-admin.md) |
| U09 | 异步自定义工具执行机制（协程工具回投主 loop 真正落地） | §6、§7 | [↗](updates/09-async-tool-execution.md) |
| U10 | 自定义工具管理（代码 BaseTool 目录登记、MCP 描述字段） | §4、§6 | [↗](updates/10-custom-tool-management.md) |
| U11 | 代码 Agent 信息透明与状态治理 | §4、§7 | [↗](updates/11-code-agent-transparency-state.md) |
| U12 | Agent 路由线索与兜底动态化 | §7 | [↗](updates/12-agent-routing-clues.md) |
| U13 | 路由分发器后台只读视图 | §5、§7 | [↗](updates/13-router-agent-readonly.md) |
| U14 | 兜底意图应答通道（`ROUTER_FALLBACK_ANSWER`） | §7、§10 | [↗](updates/14-fallback-answer-path.md) |
| U15 | 链路详情显示路由意图依据（`clue_basis`） | §7、§14 | [↗](updates/15-route-intent-evidence.md) |
| U16 | 高频固定话术（硬模板 + 后台可配） | §4、§7 | [↗](updates/16-canned-scripts-db.md) |
| U17 | IT Helpdesk 场景包与包启停（历史，机制已移除） | §3、§7 | [↗](updates/17-it-helpdesk-pack.md) |
| U18 | 接入方与 Key 归属（integrations） | §4、§12 | [↗](updates/18-integration-keys.md) |
| U19 | 匿名接入的接入方归属 | §4、§12 | [↗](updates/19-anonymous-integration-attribution.md) |
| U20 | 单租户降级与自定义埋点（chat_events） | §4、§12 | [↗](updates/20-single-tenant-degradation.md) |
| U21 | 场景身份话术、场景运行参数与登录人配额 | §4、§12、§13 | [↗](updates/21-scene-identity-runtime-quota.md) |

> 接口清单以 [`api.md`](api.md) 为准（已同步全部实际端点与 SSE 事件）；部署/运维以 [`runbook.md`](runbook.md) 为准。

---

## 目录

1. [项目基本信息](#1-项目基本信息)
2. [环境与依赖规范](#2-环境与依赖规范)
3. [代码仓库结构规范](#3-代码仓库结构规范)
4. [数据库 Schema 规范](#4-数据库-schema-规范)
5. [后端 API 规范](#5-后端-api-规范)
6. [HelloAgents 集成规范](#6-helloagents-集成规范)
7. [多 Agent 架构规范](#7-多-agent-架构规范)
8. [记忆系统规范（基于 MemoryTool）](#8-记忆系统规范基于-memorytool)
9. [RAG 系统规范（基于 RAGTool）](#9-rag-系统规范基于-ragtool)
10. [流式输出规范](#10-流式输出规范)
11. [前端规范](#11-前端规范)
12. [鉴权与安全规范](#12-鉴权与安全规范)
13. [错误码规范](#13-错误码规范)
14. [日志与可观测性规范](#14-日志与可观测性规范)
15. [测试规范](#15-测试规范)
16. [验收标准](#16-验收标准)
17. [附录](#17-附录)

---

## 1. 项目基本信息

### 1.1 项目标识

| 字段 | 值 |
|------|-----|
| 项目名 | smart-customer-service |
| 仓库名 | smart-cs |
| 后端服务名 | cs-backend |
| 前端服务名 | cs-frontend |
| 默认端口（后端） | 8000 |
| 默认端口（前端） | 5173（开发） |
| API 前缀 | `/api/v1` |
| 语言 | 中文（默认），预留 i18n |

### 1.2 核心模块清单

| 模块 | 职责 | HelloAgents 对应 |
|------|------|-----------------|
| auth | 登录、鉴权、RBAC | — |
| chat | 对话流式接口 | Orchestrator + SimpleAgent |
| memory | 四层记忆管理 | **MemoryTool** |
| rag | 文档与检索 | **RAGTool** |
| agents | 业务 Agent 独立开发 + 路由分发 | **SimpleAgent** + ToolRegistry |
| tools | MCP 与自定义工具 | ToolRegistry + BaseTool |
| admin | 系统管理与监控 | — |

### 1.3 架构总览

```
                       ┌─────────────────────────┐
   用户消息 ──────────▶│     RouterAgent          │
                       │  （路由 Agent，独立）    │
                       │  • 轻量模型 gpt-4o-mini  │
                       │  • 只做意图分类          │
                       │  • 无业务工具            │
                       └───────────┬─────────────┘
                                   │ intent + agent_name + confidence
                    ┌──────────────┼──────────────┬─────────────┐
                    ▼              ▼              ▼             ▼
        ┌───────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
        │KnowledgeAgent │ │  OrderAgent  │ │ComplaintAgent│ │  ...新增...  │
        │  知识问答业务  │ │  订单服务业务 │ │  投诉处理业务  │ │              │
        ├───────────────┤ ├──────────────┤ ├──────────────┤ ├──────────────┤
        │独立 system_   │ │独立 system_  │ │独立 system_  │ │独立 system_  │
        │prompt         │ │prompt        │ │prompt        │ │prompt        │
        ├───────────────┤ ├──────────────┤ ├──────────────┤ ├──────────────┤
        │tools:         │ │tools:        │ │tools:        │ │tools:        │
        │ MemoryTool    │ │ MemoryTool   │ │ MemoryTool   │ │ ...          │
        │ RAGTool       │ │ QueryOrder   │ │ CreateTicket │ │              │
        │               │ │ ModifyOrder  │ │ Escalate     │ │              │
        │               │ │ MCP订单工具  │ │              │ │              │
        └───────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
              独立开发        独立开发        独立开发        独立开发
```

### 1.4 多 Agent 三大核心约束

| 约束 | 说明 |
|------|------|
| **路由 Agent 与业务 Agent 分离** | 路由 Agent 不加载任何业务工具，只做分类；业务 Agent 不含路由逻辑 |
| **每个业务 Agent 独立成类** | 一个业务 = 一个 Python 类 = 一个文件，独立 Prompt + 独立工具集 |
| **注册表驱动路由** | 路由结果通过字符串 `agent_name` 映射到具体类，新增业务只需注册 |

---

## 2. 环境与依赖规范

### 2.1 版本要求（强制）

```yaml
python: ">=3.11,<3.13"        # 本仓库 backend/.venv 由 uv 管理，实测 3.12
node: ">=18.18.0"
npm / pnpm: 任一即可（README 以 npm 为例）
postgres: "14+（无需 pgvector / uuid-ossp；RAG 向量不落 PG）"
redis: "7.x"                  # 工作记忆 / 缓存
qdrant: "向量库（记忆 + RAG 真相）；qdrant-client 需 <1.10"
neo4j: "可选（仅启用 semantic 语义记忆时必需）"
hello-agents: ">=0.2.4,<=0.2.9"   # 公开版，[memory,protocols] 扩展
```

> 相比 v1.x：不再要求 PostgreSQL 15 / pgvector（向量存 Qdrant）；默认 UUID 用 PG13+ 内置 `gen_random_uuid()`，不依赖 `uuid-ossp`。

### 2.2 后端依赖（requirements.txt，节选）

以下为当前 `backend/requirements.txt` 的真实约束，关键**钉版原因**必须遵守（否则运行期会碎）：

```txt
# ============ HelloAgents 框架（核心） ============
# [memory,protocols] 扩展：memory→四层记忆（episodic 依赖 Qdrant、semantic 依赖 Neo4j/Spacy）；
# protocols→MCPTool / 协议适配（a2a-sdk）。fastapi/httpx 用下限(>=)而非钉死，避免 a2a-sdk 拉高版本时冲突。
hello-agents[memory,protocols]>=0.2.4,<=0.2.9

# ============ 向量库 / Embedding 运行时硬约束 ============
# hello-agents 0.2.9 的 qdrant_store 仍 import SearchRequest，qdrant-client>=1.10 已移除该类（导致 QDRANT_AVAILABLE=False），故钉上界。
qdrant-client>=1.9.0,<1.10
# EMBED_MODEL_TYPE=dashscope 时，DashScopeEmbedding.__init__ 无条件 import dashscope SDK（即使走 REST）。
dashscope>=1.20.0

# FastAPI 与基础（下限宽松，由解析器上探兼容版）
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
httpx>=0.27.0
aiohttp>=3.10.0
python-dotenv>=1.0.0
python-multipart>=0.0.9
loguru>=0.7.0
structlog>=24.4.0
fastmcp>=2.0.0
uv>=0.8.0
python-dateutil>=2.8.2
huggingface_hub>=0.25.0

# ============ 数据层 ============
sqlalchemy[asyncio]==2.0.35
# asyncpg 0.29.x 无 cp312/cp313 预编译 wheel（需本地 MSVC），0.30.0 起提供
asyncpg>=0.30.0,<0.31
alembic==1.13.2
redis==5.0.8

# ============ 鉴权 ============
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
# passlib 1.7.4 仅兼容 bcrypt<4.1；不锁会被拉入 bcrypt 5.x → hash_password 报 “72 bytes” 错
bcrypt==4.0.1

# ============ 缓存 / 文档解析 / 监控 / 测试 ============
cachetools==5.5.0
pypdf==5.0.1
python-docx==1.1.2
markdown==3.7
beautifulsoup4==4.12.3
markitdown[pdf,docx]>=0.1.5     # RAG pipeline 的 PDF/DOCX 增强转换
tenacity==9.0.0
prometheus-client==0.21.0
pytest==8.3.3
pytest-asyncio==0.24.0
pytest-cov==6.0.0
testcontainers==4.8.2
```

> v1.x 列的 `pgvector==0.3.2` 仍保留在依赖中（供部分历史代码导入），但 **RAG/记忆向量实际都存 Qdrant**，PG 无 vector 列；`openai==1.51.0` 已不再直接声明（由 hello-agents 间接依赖）。

### 2.3 前端依赖（package.json 关键项）

```json
{
  "dependencies": {
    "vue": "^3.5.10",
    "vue-router": "^4.4.5",
    "pinia": "^2.2.4",
    "element-plus": "^2.8.4",
    "axios": "^1.7.7",
    "markdown-it": "^14.1.0",
    "highlight.js": "^11.10.0",
    "dayjs": "^1.11.13"
  },
  "devDependencies": {
    "vite": "^5.4.8",
    "typescript": "^5.6.2",
    "eslint": "^9.11.1",
    "prettier": "^3.3.3",
    "vitest": "^2.1.2",
    "@playwright/test": "^1.47.2"
  }
}
```

### 2.4 环境变量（`.env.example`）

```env
# ===== 应用 =====
APP_ENV=development
APP_NAME=smart-cs
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO

# ===== HelloAgents LLM 配置（框架直接读取） =====
LLM_MODEL_ID=gpt-4o
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.openai.com/v1
LLM_TIMEOUT=60
LLM_MAX_RETRIES=2

# ===== Embedding（hello_agents 记忆/RAG 底层使用，框架直接读取） =====
# EMBED_MODEL_TYPE: dashscope | local | tfidf
# EMBED_MODEL_NAME 若为空：dashscope 默认 text-embedding-v3；local 默认 sentence-transformers/all-MiniLM-L6-v2
# EMBED_BASE_URL 不要以 /embeddings 结尾，框架会自动追加 /embeddings
EMBED_MODEL_TYPE=dashscope
EMBED_MODEL_NAME=text-embedding-v2
EMBED_API_KEY=sk-xxx
EMBED_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# ===== HelloAgents RAG 知识库根目录 =====
RAG_KB_ROOT=./data/knowledge_bases
RAG_DEFAULT_NAMESPACE=cs_default

# ===== 数据库 =====
DATABASE_URL=postgresql+asyncpg://cs_user:cs_pass@localhost:5432/cs_db
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10

# ===== Redis =====
REDIS_URL=redis://localhost:6379/0
WORKING_MEMORY_TTL=1800

# ===== 鉴权 =====
JWT_SECRET=change_me_in_prod
JWT_ALGORITHM=HS256
ACCESS_TOKEN_TTL=7200
REFRESH_TOKEN_TTL=604800

# ===== 记忆阈值 =====
MEMORY_IMPORTANCE_THRESHOLD=0.6
MEMORY_ACCESS_THRESHOLD=3
MEMORY_DECAY_HALF_LIFE_DAYS=30

# ===== RAG =====
RAG_CHUNK_SIZE=512
RAG_CHUNK_OVERLAP=64
RAG_TOP_K=5
RAG_MIN_SCORE=0.3

# ===== 上下文预算 =====
CONTEXT_MAX_TOKENS=8000
CONTEXT_SYSTEM_RESERVE=800
CONTEXT_HISTORY_RESERVE=2000

# ===== 路由 =====
ROUTER_MODEL=gpt-4o-mini
ROUTER_CONFIDENCE_THRESHOLD=0.5
```

### 2.5 强制约束

- 所有 Agent / 记忆 / RAG 能力**必须通过 HelloAgents 框架 API 调用**，禁止绕过框架直接调用底层 LLM 或向量库
- 所有业务 Agent 必须继承 `BaseBusinessAgent`
- 记忆一律通过 `MemoryTool`，知识库一律通过 `RAGTool`
- 工具一律通过 `ToolRegistry.register_tool()`
- 所有 Python 文件必须通过 `black` 和 `ruff` 检查
- 提交前必须运行 `pre-commit run --all-files`

---

## 3. 代码仓库结构规范

```
smart-cs/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── deps.py
│   │   │   └── v1/
│   │   │       ├── router.py
│   │   │       ├── auth.py
│   │   │       ├── chat.py
│   │   │       ├── sessions.py
│   │   │       ├── memory.py
│   │   │       ├── knowledge.py
│   │   │       ├── admin.py              # Agent/MCP 管理
│   │   │       ├── admin_inquiries.py    # 跨客户咨询 + CSV 导出
│   │   │       ├── admin_tickets.py      # 工单增改查
│   │   │       └── admin_traces.py       # 请求链路（只读）
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── security.py
│   │   │   ├── logging.py
│   │   │   ├── exceptions.py
│   │   │   ├── database.py
│   │   │   ├── tracing.py               # request_traces 采集（旁路）
│   │   │   ├── context_engineer.py
│   │   │   ├── bootstrap.py
│   │   │   └── constants.py
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   ├── base_business_agent.py
│   │   │   ├── router_agent.py
│   │   │   ├── orchestrator.py
│   │   │   ├── registry.py               # 显式登记代码 Agent（_CODE_AGENTS）
│   │   │   ├── canned_scripts.py
│   │   │   ├── llm_factory.py
│   │   │   ├── business/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── dynamic_agent.py      # 动态 Agent（内核能力）
│   │   │   │   ├── knowledge_agent.py
│   │   │   │   ├── order_agent.py
│   │   │   │   ├── complaint_agent.py
│   │   │   │   └── it_*.py               # IT 知识/故障/资产
│   │   │   └── prompts/
│   │   │       ├── __init__.py
│   │   │       ├── router.py             # 路由提示词（内核）
│   │   │       └── {knowledge,order,complaint,it_*}.py
│   │   ├── tools/
│   │   │   ├── __init__.py
│   │   │   ├── memory_factory.py
│   │   │   ├── rag_factory.py
│   │   │   ├── mcp_loader.py
│   │   │   ├── custom_catalog.py
│   │   │   ├── custom_loader.py
│   │   │   └── custom/
│   │   │       ├── __init__.py
│   │   │       ├── base.py               # BaseTool 适配层（async run(**kwargs)）
│   │   │       └── {order,logistics,refund,ticket,it_*}_tool.py + mock_*.py
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── chat_service.py
│   │   │   ├── memory_service.py
│   │   │   ├── rag_service.py
│   │   │   ├── qdrant_helper.py          # 直连 Qdrant：分块浏览/按源删除
│   │   │   └── document_service.py
│   │   └── utils/
│   │       ├── sse.py
│   │       └── text.py
│   ├── mcp_servers/              # 本地 FastMCP（stdio）演示服务
│   │   ├── logistics_server.py
│   │   └── refund_server.py
│   ├── data/                     # 运行时数据（.gitignore 排除）：doc_archives/、knowledge_bases/（RAG_KB_ROOT 相对 backend 工作目录）
│   ├── alembic/
│   ├── tests/
│   ├── pyproject.toml
│   ├── requirements.txt
│   └── .env.example
├── frontend/                     # Vue3 客户对话端（npm workspace，端口 5173）
│   ├── src/
│   │   ├── main.ts
│   │   ├── App.vue
│   │   ├── api/
│   │   ├── stores/
│   │   ├── router/
│   │   ├── views/
│   │   │   ├── customer/
│   │   │   └── LoginView.vue
│   │   ├── components/
│   │   └── types/               # 基础类型转出 @cs/shared，业务类型本地维护
│   ├── package.json
│   ├── vite.config.ts            # alias @shared -> packages/shared/src
│   └── tsconfig.json
├── admin-frontend/               # Vue3 管理后台（npm workspace，端口 5174）
│   ├── src/
│   │   ├── api/  components/  layouts/  router/  stores/  types/
│   │   └── views/               # AgentList/KnowledgeBaseView/TicketsView/
│   │                             # TracesView/MemoryView/InquiriesView/McpServersView/Dashboard
│   ├── package.json
│   └── vite.config.ts
├── packages/
│   └── shared/                   # @cs/shared：http（401 单飞刷新）/auth store/SSE/基础类型
│       └── src/
├── package.json                  # npm workspaces 根：dev:chat / dev:admin / build
├── docs/
│   ├── spec.md
│   ├── api.md
│   ├── runbook.md
│   └── updates/                 # U01~U11 各已完成功能的详解文档 + 索引 README
├── scripts/
│   ├── init_db.sh
│   ├── init_db.sql
│   ├── seed_data.py
│   ├── rebuild_and_seed.py
│   ├── seed_memory_via_chat.py
│   ├── cleanup_traces.py
│   ├── start_all.ps1
│   └── stop_all.ps1
└── README.md
```

### 3.1 命名约定（强制）

| 类型 | 规则 | 示例 |
|------|------|------|
| Python 模块 | snake_case | `memory_factory.py` |
| Python 类 | PascalCase | `MemoryFactory` |
| Python 函数/变量 | snake_case | `build_memory_tool()` |
| TS 组件 | PascalCase | `MessageBubble.vue` |
| DB 表 | 复数 snake_case | `memories` |
| API 路径 | kebab-case | `/api/v1/knowledge-base` |
| 环境变量 | UPPER_SNAKE | `LLM_API_KEY` |

---

## 4. 数据库 Schema 规范

> 说明：HelloAgents 的 `MemoryTool` 与 `RAGTool` 自带持久化，**运行时真相在 Qdrant（向量）/ Neo4j（语义，若启用）**。本系统在其上叠加 PostgreSQL 作为**业务级归档、审计与后台管理**。`memories` / `documents` 是归档镜像，非运行时检索入口。
>
> **v2.0 关键变更**：① `users` 拆为 `staff_users`（admin/agent）与 `customer_users`（可自助注册）（[U07](updates/07-staff-customer-auth-split.md)）；② 新增 `tickets` 结构化工单表（[U06](updates/06-tickets-cross-round-memory.md)）；③ 新增 `request_traces` 请求链路表（[U04](updates/04-request-tracing.md)）；④ 新增 `knowledge_bases` 命名空间主表，`documents` 加 `kind`（[U08](updates/08-knowledge-base-admin.md)）；⑤ `agents` 支持动态 Agent 列（[U03](updates/03-dynamic-agents.md)）；⑥ UUID 统一用内置 `gen_random_uuid()`，不再依赖 `uuid-ossp`/`vector`。

### 4.1 初始化 SQL（`scripts/init_db.sql`，节选）

```sql
-- 默认 UUID 用 PG13+ 内置 gen_random_uuid()，无需 uuid-ossp；RAG 向量存 Qdrant，不依赖 pgvector。
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ========== 后台人员（管理员 / 客服坐席） ==========
CREATE TABLE staff_users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(64) UNIQUE NOT NULL,
    password_hash   VARCHAR(256) NOT NULL,
    role            VARCHAR(16) NOT NULL DEFAULT 'agent'
                    CHECK (role IN ('agent','admin')),
    display_name    VARCHAR(64),
    metadata        JSONB DEFAULT '{}'::jsonb,
    -- 资源创建配额：键缺失/null=不限，0=禁止创建，正数=上限（U21）
    quota           JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_staff_users_role ON staff_users(role);

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

-- ========== 会话（user_id → customer_users；assigned_agent → staff_users） ==========
CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES customer_users(id) ON DELETE CASCADE,
    title           VARCHAR(128) DEFAULT '新对话',
    status          VARCHAR(16) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','closed','handover')),
    assigned_agent  UUID REFERENCES staff_users(id),
    memory_tool_uid VARCHAR(64),
    rag_namespace   VARCHAR(64),
    last_route_to   VARCHAR(64),        -- 上一轮路由 Agent，供追问续接
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sessions_user_created ON sessions(user_id, created_at DESC);

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
CREATE INDEX idx_messages_session_created ON messages(session_id, created_at ASC);

-- ========== 记忆归档（MemoryTool 的镜像，user_id → customer_users） ==========
CREATE TABLE memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES customer_users(id) ON DELETE CASCADE,
    memory_uid      VARCHAR(64) NOT NULL,
    session_id      UUID REFERENCES sessions(id) ON DELETE SET NULL,
    -- 溯源：产生该记忆的当轮用户消息（content 与该行文本同源，此列建立关联而非重复存储）
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
    UNIQUE(user_id, memory_uid)
);
CREATE INDEX idx_memories_user_type ON memories(user_id, type);
CREATE INDEX idx_memories_source_message ON memories(source_message_id);
CREATE INDEX idx_memories_importance ON memories(importance DESC);
CREATE INDEX idx_memories_content_trgm ON memories USING gin (content gin_trgm_ops);

-- ========== 知识库命名空间（管理后台维护，namespace 主键） ==========
CREATE TABLE knowledge_bases (
    namespace     VARCHAR(64) PRIMARY KEY,
    display_name  VARCHAR(128) NOT NULL,
    description   TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========== 知识库文档归档（kind: upload|manual） ==========
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           VARCHAR(256) NOT NULL,
    source          VARCHAR(512),          -- 向量 source_path（Qdrant 侧按此删除）
    content_type    VARCHAR(64),
    size_bytes      BIGINT,
    kind            VARCHAR(16) NOT NULL DEFAULT 'upload'
                    CHECK (kind IN ('upload','manual')),
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','processing','ready','failed')),
    error_message   TEXT,
    rag_namespace   VARCHAR(64) NOT NULL,
    chunk_count     INT NOT NULL DEFAULT 0,
    uploaded_by     UUID REFERENCES staff_users(id),
    metadata        JSONB DEFAULT '{}'::jsonb,   -- 存 archive_path / original_filename（原文归档）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_namespace ON documents(rag_namespace);
CREATE INDEX idx_documents_kind ON documents(kind);

-- ========== Agent 元数据注册（代码托管 code + 动态后台 dynamic 混合） ==========
CREATE TABLE agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(64) UNIQUE NOT NULL,
    display_name    VARCHAR(128),
    description     TEXT,
    routing_keywords JSONB NOT NULL DEFAULT '[]'::jsonb,    -- 路由线索：触发关键词
    routing_examples JSONB NOT NULL DEFAULT '[]'::jsonb,    -- 路由线索：典型用户问法
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order      INT DEFAULT 0,
    source          VARCHAR(16) NOT NULL DEFAULT 'code',   -- code | dynamic
    system_prompt   TEXT,                                   -- dynamic 必填
    llm_model       VARCHAR(128) NOT NULL DEFAULT '',       -- 空则用默认 LLM_MODEL_ID
    rag_namespace   VARCHAR(64),
    search_all_knowledge_bases BOOLEAN NOT NULL DEFAULT FALSE,  -- 通用问答覆盖全部启用知识库
    mcp_tool_names  JSONB NOT NULL DEFAULT '[]'::jsonb,     -- 动态 Agent ↔ MCP 工具绑定
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========== 工具注册（含 MCP 服务 config：transport/endpoint|command/args/timeout） ==========
CREATE TABLE tools (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(64) UNIQUE NOT NULL,
    type            VARCHAR(16) NOT NULL CHECK (type IN ('mcp','custom','builtin')),
    schema          JSONB NOT NULL,
    config          JSONB DEFAULT '{}'::jsonb,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
CREATE INDEX idx_audit_user_created ON audit_logs(user_id, created_at DESC);
CREATE INDEX idx_audit_action ON audit_logs(action);

-- ========== 客诉工单（结构化增改查，取代此前埋在 audit_logs.payload 的工单记录） ==========
CREATE TABLE IF NOT EXISTS tickets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_no   VARCHAR(32) UNIQUE NOT NULL,       -- TK-XXXXXXXX，业务与后台都以它回查
    user_id     UUID,                              -- UUID 但不建外键（示例客户名非 UUID 会降级为 NULL）
    session_id  UUID,
    category    VARCHAR(32) NOT NULL DEFAULT 'general',
    urgency     VARCHAR(16) NOT NULL DEFAULT 'medium' CHECK (urgency IN ('low','medium','high')),
    status      VARCHAR(16) NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','escalated','resolved','closed')),
    title       VARCHAR(256),
    content     TEXT NOT NULL DEFAULT '',
    updates     JSONB NOT NULL DEFAULT '[]'::jsonb, -- 追加式补录历史（每项带 UTC 时间戳）
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tickets_status_created ON tickets(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_user_created ON tickets(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_session ON tickets(session_id);

-- ========== 请求链路（一轮对话一行，明细存 spans JSONB） ==========
CREATE TABLE IF NOT EXISTS request_traces (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id            VARCHAR(64) NOT NULL,       -- 与 structlog / X-Trace-Id 一致
    session_id          UUID,
    user_id             UUID,
    user_message_id     UUID,
    assistant_message_id UUID,
    intent_count        INT NOT NULL DEFAULT 0,
    agent_names         TEXT[] DEFAULT '{}',         -- 高频过滤维度拍平为数组列，GIN 命中
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
    outcome             VARCHAR(16),                 -- answered | handover | error
    handover_reason     VARCHAR(32),
    error_code          INT,
    error_message       TEXT,
    ttft_ms             INT,
    total_latency_ms    INT,
    response_chars      INT NOT NULL DEFAULT 0,
    spans               JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 时间线明细（router.llm/agent.build/tool.call/...）
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_traces_trace_id ON request_traces(trace_id);
CREATE INDEX IF NOT EXISTS idx_traces_assistant_msg ON request_traces(assistant_message_id);
CREATE INDEX IF NOT EXISTS idx_traces_session_created ON request_traces(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_user_created ON request_traces(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_outcome_created ON request_traces(outcome, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_agents ON request_traces USING gin (agent_names);
CREATE INDEX IF NOT EXISTS idx_traces_tools ON request_traces USING gin (tool_names);
CREATE INDEX IF NOT EXISTS idx_traces_mcp ON request_traces USING gin (mcp_server_names);

-- ========== 业务场景（无 scene_code 的请求落默认场景） ==========
CREATE TABLE scenes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(32) UNIQUE NOT NULL,          -- 对外场景码，创建后不可改
    display_name    VARCHAR(64) NOT NULL,
    description     TEXT,
    status          VARCHAR(16) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','paused','archived')),
    is_default      BOOLEAN NOT NULL DEFAULT FALSE,       -- 全库唯一（部分唯一索引）
    router_mode     VARCHAR(16) NOT NULL DEFAULT 'auto'
                    CHECK (router_mode IN ('auto','llm','direct')),
    fallback_agent_id UUID,                               -- 不建外键
    runtime_config  JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 场景运行参数，{} = 全部继承全局（U21）
    widget_config   JSONB NOT NULL DEFAULT '{}'::jsonb,
    allowed_origins JSONB NOT NULL DEFAULT '[]'::jsonb,
    publish_key     VARCHAR(64),                          -- 前端嵌入公开标识（非密钥）
    created_by      UUID,                                 -- 创建人，登录人配额按此统计（U21）
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_scenes_single_default ON scenes(is_default) WHERE is_default;
CREATE INDEX idx_scenes_created_by ON scenes(created_by);

-- ========== 场景 ↔ Agent（多对多；enabled 只影响本场景） ==========
CREATE TABLE scene_agents (
    scene_id        UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order      INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (scene_id, agent_id)
);
CREATE INDEX idx_scene_agents_scene_enabled ON scene_agents(scene_id, enabled, sort_order);

-- ========== 固定话术（scene_id NULL = 全局模板，同 key 场景行覆盖全局） ==========
CREATE TABLE canned_scripts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id      UUID REFERENCES scenes(id) ON DELETE CASCADE,
    key           VARCHAR(64) NOT NULL,                   -- 业务可读标识
    title         VARCHAR(128),
    category      VARCHAR(16) NOT NULL DEFAULT 'business',-- identity | capability | probing | business
    patterns      JSONB NOT NULL DEFAULT '[]'::jsonb,     -- 问法主干（整句全等）
    keywords      JSONB NOT NULL DEFAULT '[]'::jsonb,     -- 关键词（包含即命中）
    reply_text    TEXT NOT NULL DEFAULT '',
    answer_source VARCHAR(16) NOT NULL DEFAULT 'text'     -- text=逐字下发；kb=检索后拼接（U21）
                  CHECK (answer_source IN ('text','kb')),
    kb_namespaces JSONB NOT NULL DEFAULT '[]'::jsonb,     -- answer_source=kb 的检索范围
    priority      INT NOT NULL DEFAULT 0,                 -- 小者优先
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    is_builtin    BOOLEAN NOT NULL DEFAULT FALSE,         -- 可改文案/停用，不可删
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_canned_scripts_scene_key UNIQUE NULLS NOT DISTINCT (scene_id, key)
);
CREATE INDEX idx_canned_scripts_scene_enabled_priority
    ON canned_scripts(scene_id, enabled, priority);

-- ========== schema 版本（启动时断言；不匹配请全量重建） ==========
-- 当前值 6；新增列/表必须同时提升 init_db.sql 与 app 侧的 SCHEMA_VERSION
INSERT INTO schema_meta (key, value) VALUES ('version', '6')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
```

> `agents` / `knowledge_bases` 同样带 `created_by`（代码 Agent 与种子知识库为 `NULL`，不计入任何人的配额）；`chat_events` 是 U20 引入的全量埋点表，带 `(scene_code, created_at)` 索引。

### 4.2 迁移与 Schema 演进

- **本项目处开发阶段，schema 频繁变动**（如 `users` 拆分、工单迁表、链路表新增）。改 ORM 模型后**无需手写增量迁移**，直接对目标库全量重建 + 灌种子（⚠️ 清空业务数据，仅限开发）：`python scripts/rebuild_and_seed.py`（DROP 旧表含遗留 `users` → `create_all` 建表 → seed；单往返 simple query 适配云库代理）。
- `init_db.sql` 与 ORM（`models/entities.py`）保持一致；新增列/表在 SQL 里用 `CREATE TABLE IF NOT EXISTS` 便于既有库增量补建。
- `schema_meta.version` 是唯一的结构版本源（当前 `6`）：后端启动断言，`scripts/verify_schema.py` 校验，不匹配直接报错而不是带病运行。版本历史：`2`（多场景）→ `3`（接入方三表 + `visitors.integration_id`）→ `4`（`integrations.public_code`）→ `5`（`chat_events`）→ `6`（`canned_scripts.answer_source/kb_namespaces`、`scenes.runtime_config/created_by`、`agents.created_by`、`knowledge_bases.created_by`、`staff_users.quota`）。
- 进入稳定期后再引入 Alembic：`alembic upgrade head` / `alembic downgrade -1`，提交前双向测试。

---

## 5. 后端 API 规范

### 5.1 通用约定

| 项 | 规范 |
|----|------|
| Base URL | `/api/v1` |
| 请求格式 | `application/json`（文件上传用 multipart） |
| 响应格式 | `{ "code": 0, "message": "ok", "data": {...} }` |
| 分页 | `?page=1&page_size=20`，响应含 `total`、`page`、`page_size` |
| 时间格式 | ISO 8601 UTC |
| ID 类型 | UUID v4 字符串 |
| 版本控制 | 路径版本（`/api/v1`） |

### 5.2 统一响应模型

```python
# schemas/common.py
from pydantic import BaseModel
from typing import Generic, TypeVar, Optional

T = TypeVar("T")

class ApiResponse(BaseModel, Generic[T]):
    code: int = 0
    message: str = "ok"
    data: Optional[T] = None

class PaginatedData(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
```

### 5.3 鉴权接口

> 人员/客户分表后登录入口分离（[U07](updates/07-staff-customer-auth-split.md)）：后台 `POST /auth/staff/login`（读 `staff_users`）、客户 `POST /auth/customer/login`（读 `customer_users`，账号不存在返回 `2004`）、客户自助注册 `POST /auth/customer/register`。以下以 `POST /auth/customer/login` 为例：

#### `POST /api/v1/auth/customer/login`

```json
// 请求
{ "username": "alice", "password": "secret" }

// 响应
{
  "code": 0,
  "data": {
    "access_token": "eyJ...",
    "refresh_token": "eyJ...",
    "token_type": "bearer",
    "expires_in": 7200,
    "user": { "id": "uuid", "username": "alice", "role": "customer" }
  }
}
```

#### `POST /api/v1/auth/refresh`

#### `GET /api/v1/auth/me`

### 5.4 对话接口（核心）

#### `POST /api/v1/chat/stream`

**请求头**：`Authorization: Bearer <token>`、`Accept: text/event-stream`

**请求体：**

```json
{
  "session_id": "uuid-or-null",
  "message": "我的订单什么时候到？",
  "stream": true
}
```

**响应事件类型：**

| event type | data 结构 | 说明 |
|-----------|-----------|------|
| `session` | `{"session_id":"uuid"}` | 首帧返回会话 ID |
| `route` | `{"agent_name","confidence","reason","intents":[...],"intent_total"}` | 路由结果；`agent_name/confidence/reason` 为主意图，`intents` 为按主次排序的意图清单（最多 `ROUTER_MAX_INTENTS` 个）（[U02](updates/02-multi-intent-routing.md)） |
| `agent_start` | `{"agent_name","intent_index","intent_total","confidence","reason"}` | 一个意图分支开始应答 |
| `agent_end` | `{"agent_name","intent_index","intent_total"}` | 该意图分支应答结束 |
| `text` | `{"content":"您"}` | 文本增量（多意图时次要分支前先下发 `\n\n---\n\n` 分块分隔） |
| `tool_call` | `{"id":"call_1","name":"query_order","args":{...}}` | 工具调用开始 |
| `tool_result` | `{"id":"call_1","name":"query_order","result":{...},"error":null}` | 工具返回（[U09](updates/09-async-tool-execution.md) 真实执行） |
| `memory_write` | `{"type":"episodic","id":"uuid"}` | 记忆写入通知 |
| `handover` | `{"reason":"low_confidence","confidence":0.3}` | 转人工 |
| `done` | `{"message_id":"uuid","trace_id":"..."}` | 结束；`trace_id` 可在后台链路页定位本轮（[U04](updates/04-request-tracing.md)） |
| `error` | `{"code":5001,"message":"..."}` | 错误 |

**示例：**

```
event: session
data: {"session_id":"a1b2..."}

event: route
data: {"agent_name":"order_service","confidence":0.92}

event: text
data: {"content":"您"}

event: text
data: {"content":"好"}

event: tool_call
data: {"id":"call_1","name":"query_order","args":{"order_id":"ORD-001"}}

event: tool_result
data: {"id":"call_1","name":"query_order","result":{"status":"已发货"},"error":null}

event: text
data: {"content":"您的订单已发货"}

event: done
data: {"message_id":"m1","usage":{"prompt_tokens":100,"completion_tokens":50}}
```

#### `POST /api/v1/chat/message`（非流式，备用）

### 5.5 会话与历史

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/sessions` | 会话列表（分页） |
| POST | `/api/v1/sessions` | 新建会话 |
| GET | `/api/v1/sessions/{id}` | 会话详情 |
| PATCH | `/api/v1/sessions/{id}` | 更新标题/状态 |
| DELETE | `/api/v1/sessions/{id}` | 删除会话 |
| GET | `/api/v1/sessions/{id}/messages` | 历史消息（分页） |

### 5.6 记忆接口（包装 MemoryTool）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v1/memory/users/{user_id}` | 查看某用户记忆 | agent/admin |
| GET | `/api/v1/memory/me` | 查看自己记忆 | customer |
| POST | `/api/v1/memory/search` | 语义检索记忆 | agent/admin |
| PATCH | `/api/v1/memory/{id}` | 修正记忆 | agent/admin |
| DELETE | `/api/v1/memory/{id}` | 遗忘记忆 | agent/admin |

### 5.7 知识库接口（包装 RAGTool）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/knowledge/documents` | 上传文档（multipart） |
| GET | `/api/v1/knowledge/documents` | 文档列表 |
| DELETE | `/api/v1/knowledge/documents/{id}` | 删除文档 |
| POST | `/api/v1/knowledge/documents/{id}/reindex` | 重新索引 |
| POST | `/api/v1/knowledge/search` | 检索测试 |

### 5.8 管理接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/admin/agents` | Agent 列表（代码注册表 + DB 状态合并） |
| GET | `/api/v1/admin/agents/{name}` | Agent 详情 |
| PATCH | `/api/v1/admin/agents/{name}/toggle` | 启用/禁用 |
| GET | `/api/v1/admin/tools` | 工具列表 |
| GET | `/api/v1/admin/metrics` | 监控指标 |

---

## 6. HelloAgents 集成规范

> **本章核心**：所有 Agent、MemoryTool、RAGTool 均基于 HelloAgents 框架原生 API 创建。

### 6.1 HelloAgents 核心组件一览

| 组件 | 导入路径 | 作用 |
|------|---------|------|
| `HelloAgentsLLM` | `hello_agents.llm` | 统一 LLM 客户端 |
| `SimpleAgent` | `hello_agents.agents` | 基础 Agent（ReAct 循环） |
| `MemoryTool` | `hello_agents.tools` | 四层记忆工具 |
| `RAGTool` | `hello_agents.tools` | 知识库检索工具 |
| `ToolRegistry` | `hello_agents.tools` | 工具注册中心 |
| `MCPTool` | `hello_agents.tools` | MCP Server 适配器 |
| `BaseTool` | `hello_agents.tools` | 自定义工具基类 |

### 6.2 启动时初始化模板

**文件：`backend/app/core/bootstrap.py`**

```python
"""
HelloAgents 框架启动初始化
系统启动时执行一次，构建全局共享的 LLM、工具注册中心、默认 Agent 实例
"""
import os
import logging
from hello_agents.llm import HelloAgentsLLM
from hello_agents.tools import MemoryTool, RAGTool, ToolRegistry
from hello_agents.agents import SimpleAgent

logger = logging.getLogger(__name__)


class HelloAgentsBootstrap:
    """HelloAgents 启动引导器（单例）"""

    def __init__(self):
        self.llm: HelloAgentsLLM | None = None
        self.memory_tool: MemoryTool | None = None
        self.rag_tool: RAGTool | None = None
        self.tool_registry: ToolRegistry | None = None
        self.agent: SimpleAgent | None = None

    def init_all(self) -> None:
        # 1. 初始化工具
        logger.info("1. 初始化工具...")

        # ---- MemoryTool：四层记忆（系统级默认实例） ----
        self.memory_tool = MemoryTool(
            user_id="system_bootstrap_user",
            memory_types=["working", "episodic", "semantic", "perceptual"],
        )

        # ---- RAGTool：知识库检索 ----
        self.rag_tool = RAGTool(
            knowledge_base_path=os.getenv("RAG_KB_ROOT", "./data/knowledge_bases"),
            rag_namespace=os.getenv("RAG_DEFAULT_NAMESPACE", "cs_default"),
        )

        logger.info("✅ MemoryTool 和 RAGTool 初始化完成")

        # 2. 注册工具
        logger.info("2. 注册工具...")
        self.tool_registry = ToolRegistry()
        self.tool_registry.register_tool(self.memory_tool)
        self.tool_registry.register_tool(self.rag_tool)
        logger.info("✅ 工具注册完成")

        # 3. 创建默认 Agent
        logger.info("3. 创建 Agent...")
        self.llm = HelloAgentsLLM()
        self.agent = SimpleAgent(
            name="智能客服助手",
            llm=self.llm,
            system_prompt=(
                "你是一名专业的智能客服助手。"
                "请优先基于记忆与知识库回答客户问题；"
                "涉及订单、工单等业务时，调用相应工具；"
                "无法确定时，转人工处理。"
            ),
            tool_registry=self.tool_registry,
        )
        logger.info("✅ Agent 创建完成")

    def shutdown(self) -> None:
        self.agent = None
        self.tool_registry = None
        self.rag_tool = None
        self.memory_tool = None
        self.llm = None


# 全局单例
bootstrap = HelloAgentsBootstrap()


def init_hello_agents() -> HelloAgentsBootstrap:
    """在 FastAPI lifespan 中调用"""
    bootstrap.init_all()
    return bootstrap


async def validate_agents_sync() -> None:
    """启动时校验：代码注册表 与 DB 中 source=code 的行 是否一致（dynamic 不参与）"""
    from app.agents.registry import AGENT_REGISTRY
    from app.models.repositories import AgentRepo

    all_rows = await AgentRepo.list_all()
    code_db_names = {r.name for r in all_rows if r.source == "code"}
    code_names = set(AGENT_REGISTRY.keys())

    orphans = code_db_names - code_names
    if orphans:
        # 代码已下线但库中仍残留 source=code 的行：不再阻断启动，
        # 这类行在后台标为“已失效”并可手动删除（详见 updates/11）。
        logger.warning(f"DB 中存在未在代码注册的 code Agent（已失效，可在后台删除）: {sorted(orphans)}")

    for missing in code_names - code_db_names:  # 只补登记缺失行，不重置已有行的 enabled
        cls = AGENT_REGISTRY[missing]
        await AgentRepo.create(
            name=missing,
            display_name=cls.description[:64] if cls.description else missing,
        )
        logger.info(f"自动登记 Agent: {missing}")
```

**在 `main.py` 中挂载：**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.bootstrap import init_hello_agents, validate_agents_sync, bootstrap

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_hello_agents()
    await validate_agents_sync()
    yield
    bootstrap.shutdown()

app = FastAPI(lifespan=lifespan)
```

### 6.3 MemoryTool 按用户动态构建

**文件：`backend/app/tools/memory_factory.py`**

```python
"""
MemoryFactory：为每个客户构建独立的 MemoryTool
缓存策略：同一 user_id 在 TTL 内复用
"""
from cachetools import TTLCache
from hello_agents.tools import MemoryTool

_memory_cache: TTLCache[str, MemoryTool] = TTLCache(maxsize=2000, ttl=1800)


def get_memory_tool(user_id: str) -> MemoryTool:
    if user_id in _memory_cache:
        return _memory_cache[user_id]

    tool = MemoryTool(
        user_id=user_id,
        memory_types=["working", "episodic", "semantic", "perceptual"],
    )
    _memory_cache[user_id] = tool
    return tool


def invalidate_memory_tool(user_id: str) -> None:
    _memory_cache.pop(user_id, None)
```

### 6.4 RAGTool 按 namespace 动态构建

**文件：`backend/app/tools/rag_factory.py`**

```python
"""
RAGFactory：为每个知识库 namespace 构建独立的 RAGTool
"""
import os
from cachetools import TTLCache
from hello_agents.tools import RAGTool

_rag_cache: TTLCache[str, RAGTool] = TTLCache(maxsize=64, ttl=3600)
KB_ROOT = os.getenv("RAG_KB_ROOT", "./data/knowledge_bases")


def get_rag_tool(namespace: str) -> RAGTool:
    if namespace in _rag_cache:
        return _rag_cache[namespace]

    tool = RAGTool(
        knowledge_base_path=KB_ROOT,
        rag_namespace=namespace,
    )
    _rag_cache[namespace] = tool
    return tool


def invalidate_rag_tool(namespace: str) -> None:
    _rag_cache.pop(namespace, None)
```

### 6.5 MCP 工具加载

**文件：`backend/app/tools/mcp_loader.py`**

```python
"""
MCPLoader：从 DB 读取 MCP 配置，加载为 MCPTool 并注册
"""
from hello_agents.tools import MCPTool, ToolRegistry
import logging

logger = logging.getLogger(__name__)


async def _get_mcp_config(name: str) -> dict | None:
    from app.models.tool import ToolRepo
    return await ToolRepo.get_by_name_and_type(name, "mcp")


def load_mcp_tools_by_name(registry: ToolRegistry, names: list[str]) -> None:
    import asyncio
    for name in names:
        cfg = asyncio.get_event_loop().run_until_complete(_get_mcp_config(name))
        if not cfg:
            logger.warning(f"MCP 配置不存在: {name}")
            continue
        try:
            tool = MCPTool(
                endpoint=cfg["config"]["endpoint"],
                tool_name=cfg["name"],
                transport=cfg["config"].get("transport", "http"),
                auth=cfg["config"].get("auth"),
                timeout=cfg["config"].get("timeout_seconds", 15),
            )
            registry.register_tool(tool)
            logger.info(f"✅ MCP 工具注册成功: {name}")
        except Exception as e:
            logger.error(f"MCP 工具注册失败: {name}, err={e}")
```

---

## 7. 多 Agent 架构规范

> **本章核心**：每个业务 Agent 独立开发（独立 Prompt、独立工具、独立类），由路由 Agent 分发。

### 7.1 架构澄清

**正确理解**：
> **每个业务一个独立的 Agent 类**，独立开发、独立测试、独立部署 Prompt 与工具集；**路由 Agent 只做意图识别**，识别后分发给对应业务 Agent。

**核心约束**：

| 约束 | 说明 |
|------|------|
| 路由 Agent 与业务 Agent 分离 | 路由 Agent 不加载任何业务工具，只做分类 |
| 每个业务 Agent 独立成类 | 一个业务 = 一个 Python 类 = 一个文件 |
| 注册表驱动路由 | 路由结果通过 `agent_name` 映射到具体类 |

### 7.2 业务 Agent 抽象基类

**文件：`backend/app/agents/base_business_agent.py`**

```python
"""
BaseBusinessAgent：所有业务 Agent 的抽象基类
"""
from abc import ABC, abstractmethod
from hello_agents.llm import HelloAgentsLLM
from hello_agents.tools import ToolRegistry
from hello_agents.agents import SimpleAgent


class BaseBusinessAgent(ABC):
    # ---- 子类必须声明的类属性 ----
    name: str = ""
    description: str = ""
    system_prompt: str = ""
    llm_model: str = "gpt-4o"
    max_iterations: int = 5
    rag_namespace: str | None = None
    mcp_tools: list[str] = []

    def __init__(self, user_id: str, session_id: str | None = None):
        self.user_id = user_id
        self.session_id = session_id

    @abstractmethod
    def build_tools(self, registry: ToolRegistry) -> None:
        """每个业务 Agent 独立实现自己的工具装配"""
        ...

    def build(self) -> SimpleAgent:
        registry = ToolRegistry()

        # 1. 业务专属工具
        self.build_tools(registry)

        # 2. MCP 工具（按业务声明加载）
        if self.mcp_tools:
            from app.tools.mcp_loader import load_mcp_tools_by_name
            load_mcp_tools_by_name(registry, self.mcp_tools)

        # 3. 创建 SimpleAgent
        return SimpleAgent(
            name=self.name,
            llm=HelloAgentsLLM(model=self.llm_model),
            system_prompt=self.system_prompt,
            tool_registry=registry,
        )
```

### 7.3 路由 Agent（独立，不含业务工具）

**文件：`backend/app/agents/router_agent.py`**

```python
"""
RouterAgent：路由 Agent
- 独立于所有业务 Agent
- 只做意图识别与分发决策
- 不加载任何业务工具
"""
import json
import logging
from hello_agents.llm import HelloAgentsLLM
from hello_agents.agents import SimpleAgent
from app.agents.prompts.router import ROUTER_SYSTEM_PROMPT
from app.agents.registry import AGENT_REGISTRY

logger = logging.getLogger(__name__)


class RouterAgent:
    def __init__(self):
        self.agent = SimpleAgent(
            name="路由分发器",
            llm=HelloAgentsLLM(model="gpt-4o-mini"),
            system_prompt=self._build_prompt(),
            tool_registry=None,       # 不加载任何工具
        )

    def _build_prompt(self) -> str:
        agent_list = "\n".join(
            f"- {name}: {cls.description}"
            for name, cls in AGENT_REGISTRY.items()
        )
        return ROUTER_SYSTEM_PROMPT.format(agent_list=agent_list)

    async def route(self, query: str) -> dict:
        raw = await self.agent.run(query)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"路由结果解析失败: {raw!r}")
            return {"agent_name": "knowledge_qa", "confidence": 0.3, "reason": "解析失败"}
        return data
```

**文件：`backend/app/agents/prompts/router.py`**

> 规则不得硬编码任何具体 Agent 名（旧版曾写“涉及订单→order_service”等具名优先条款，
> 会使后台新建的动态 Agent 抢不到路由）。“何时选我”由各 Agent 的路由线索提供：
> 代码 Agent 在 Python 类属性 `routing_keywords` / `routing_examples` 声明，
> 动态 Agent 在 `agents.routing_keywords` / `agents.routing_examples` 维护（后台表单可编）。
> 兜底 Agent 名由生效清单派生（`RouterAgent.resolve_fallback_agent`，首选 `knowledge_qa`，
> 被禁用则降级到首个生效 Agent），作为 `{fallback_agent}` 注入提示词。

```python
ROUTER_SYSTEM_PROMPT = """你是智能客服系统的路由分发器。
根据用户消息（必要时结合最近对话），判断应由哪些业务 Agent 处理，并按主次顺序输出意图。

可用业务 Agent（每个 Agent 列出其适用范围、触发关键词、典型问法）：
{agent_list}

最近对话（可能为“（无）”）：
{conversation}

上一轮承接的业务 Agent（可能为“（无）”）：
{last_agent}

请只输出 JSON（不要任何解释、不要代码块标记）：
{{
  "intents": [
    {{
      "agent_name": "<上述业务 Agent 名称之一>",
      "confidence": 0.0~1.0 之间的置信度,
      "reason": "一句话说明分类依据"
    }}
  ]
}}

规则：
- 一条消息最多输出 {max_intents} 个意图，按“主意图在前”排序；只有一个诉求时只输出 1 个元素
- agent_name 必须逐字照抄清单中冒号前的名称，不得重复、不得臆造、不得自行翻译或改写
- 选择依据只看各 Agent 声明的适用范围/关键词/典型问法与用户消息的贴合度，不要凭名称猜测职责，也不要偏向某个特定 Agent
- 用户消息同时落入多个 Agent 的适用范围时（如既要查订单又要开票），按主次输出多个意图
- 若当前消息只是对上一轮的追问或补充，只输出上一轮承接的业务 Agent 一个意图
- 与任何 Agent 的适用范围都不贴合时，只输出一个意图：agent_name 填 {fallback_agent}，confidence 填 0.3
"""
```

`{agent_list}` 的渲染格式（`RouterAgent._format_agent_list`，线索缺失时降级为一行描述）：

```
- invoice_service：开票服务：受理开票申请、修改抬头
  关键词：发票、开票、抬头、税号
  典型问法：帮我开张发票 / 发票抬头写错了能改吗
```

### 7.4 业务 Agent 示例（每个独立开发）

#### 7.4.1 知识问答 Agent

**文件：`backend/app/agents/business/knowledge_agent.py`**

```python
from hello_agents.tools import ToolRegistry
from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.prompts.knowledge import KNOWLEDGE_SYSTEM_PROMPT
from app.tools.memory_factory import get_memory_tool
from app.tools.rag_factory import get_rag_tool


class KnowledgeAgent(BaseBusinessAgent):
    name = "knowledge_qa"
    description = "通用知识问答：产品FAQ、公司政策、使用指南"
    system_prompt = KNOWLEDGE_SYSTEM_PROMPT
    llm_model = "gpt-4o"
    rag_namespace = "cs_default"

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(get_rag_tool(self.rag_namespace))
```

**文件：`backend/app/agents/prompts/knowledge.py`**

```python
KNOWLEDGE_SYSTEM_PROMPT = """你是公司知识问答专员，负责回答产品、政策、使用指南类问题。

工作方式：
1. 优先检索【知识库】中的内容回答问题
2. 结合【记忆】中的客户历史偏好，个性化回答
3. 若知识库中没有答案，明确告知客户，并建议转人工
4. 回答简洁、专业，使用客户易于理解的语言

禁止：
- 编造知识库中没有的信息
- 承诺无法确认的服务条款
"""
```

#### 7.4.2 订单服务 Agent

**文件：`backend/app/agents/business/order_agent.py`**

```python
from hello_agents.tools import ToolRegistry
from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.prompts.order import ORDER_SYSTEM_PROMPT
from app.tools.memory_factory import get_memory_tool
from app.tools.custom.order_tool import QueryOrderTool, ModifyOrderTool
from app.tools.custom.logistics_tool import QueryLogisticsTool


class OrderAgent(BaseBusinessAgent):
    name = "order_service"
    description = "订单服务：查询订单、物流追踪、修改订单、退换申请"
    system_prompt = ORDER_SYSTEM_PROMPT
    llm_model = "gpt-4o"
    rag_namespace = "cs_order"

    # 本业务使用的 MCP 工具
    mcp_tools = ["mcp_logistics", "mcp_refund"]

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(QueryOrderTool())
        registry.register_tool(ModifyOrderTool())
        registry.register_tool(QueryLogisticsTool())
```

**文件：`backend/app/agents/prompts/order.py`**

```python
ORDER_SYSTEM_PROMPT = """你是订单服务专员，负责处理订单查询、物流追踪、订单修改、退换申请。

工作方式：
1. 客户询问订单状态时，先索取订单号（若客户已提供则直接使用）
2. 调用 query_order 或 query_logistics 工具获取真实数据
3. 修改订单前必须向客户确认修改内容
4. 退换申请需调用 mcp_refund 工具，并说明退换政策
5. 结合【记忆】中该客户的历史订单偏好，提供主动服务

禁止：
- 未调用工具就编造订单状态
- 直接承诺退款金额
- 泄露其他客户订单信息
"""
```

#### 7.4.3 投诉处理 Agent

**文件：`backend/app/agents/business/complaint_agent.py`**

```python
from hello_agents.tools import ToolRegistry
from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.prompts.complaint import COMPLAINT_SYSTEM_PROMPT
from app.tools.memory_factory import get_memory_tool
from app.tools.custom.ticket_tool import CreateTicketTool, EscalateTool


class ComplaintAgent(BaseBusinessAgent):
    name = "complaint_handler"
    description = "投诉处理：受理客户投诉、安抚情绪、创建工单、升级人工"
    system_prompt = COMPLAINT_SYSTEM_PROMPT
    llm_model = "gpt-4o"
    rag_namespace = "cs_after_sales"

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(CreateTicketTool())
        registry.register_tool(EscalateTool())
```

**文件：`backend/app/agents/prompts/complaint.py`**

```python
COMPLAINT_SYSTEM_PROMPT = """你是投诉处理专员，负责受理客户投诉并推动解决。

工作方式：
1. 先共情、道歉，稳定客户情绪（禁止争辩）
2. 详细记录投诉内容（5W1H）
3. 调用 create_ticket 创建工单，告知客户工单号
4. 若客户情绪激动或问题严重，调用 escalate 升级人工
5. 结合【记忆】避免重复询问已提供的信息

禁止：
- 与客户争执或推卸责任
- 承诺无法实现的赔偿
- 在客户情绪未稳定前讨论细节
"""
```

### 7.5 Agent 注册表

**文件：`backend/app/agents/registry.py`**

```python
"""
AgentRegistry：业务 Agent 类注册表
每个业务 Agent 是一个类，独立开发，注册到这里即可被路由分发
"""
from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.business.knowledge_agent import KnowledgeAgent
from app.agents.business.order_agent import OrderAgent
from app.agents.business.complaint_agent import ComplaintAgent

# 新增业务 Agent：只需 import 并加入此表
AGENT_REGISTRY: dict[str, type[BaseBusinessAgent]] = {
    KnowledgeAgent.name: KnowledgeAgent,
    OrderAgent.name: OrderAgent,
    ComplaintAgent.name: ComplaintAgent,
    # RefundAgent.name: RefundAgent,
}


def get_agent_class(name: str) -> type[BaseBusinessAgent] | None:
    return AGENT_REGISTRY.get(name)


def list_agents() -> list[dict]:
    return [
        {
            "name": cls.name,
            "description": cls.description,
            "model": cls.llm_model,
            "rag_namespace": cls.rag_namespace,
            "mcp_tools": cls.mcp_tools,
        }
        for cls in AGENT_REGISTRY.values()
    ]
```

### 7.6 Orchestrator 调度器

**文件：`backend/app/agents/orchestrator.py`**

```python
"""
Orchestrator：调度器
  1. 调用 RouterAgent 识别意图
  2. 从注册表取出业务 Agent 类
  3. 实例化业务 Agent
  4. 构建 SimpleAgent 并流式执行
"""
import logging
from typing import AsyncIterator

from app.agents.router_agent import RouterAgent
from app.agents.registry import get_agent_class
from app.core.context_engineer import ContextEngineer

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.5


class Orchestrator:
    def __init__(self):
        self.router = RouterAgent()
        self.ctx_engineer = ContextEngineer()

    async def dispatch(
        self,
        user_id: str,
        session_id: str,
        query: str,
        history: list[dict],
    ) -> AsyncIterator[dict]:
        # ---- 1. 路由 ----
        intent = await self.router.route(query)
        agent_name = intent.get("agent_name", "knowledge_qa")
        confidence = float(intent.get("confidence", 0.0))
        logger.info(f"[route] {agent_name} conf={confidence} reason={intent.get('reason')}")

        yield {
            "type": "route",
            "data": {
                "agent_name": agent_name,
                "confidence": confidence,
                "reason": intent.get("reason", ""),
            },
        }

        # ---- 2. 低置信度 → 转人工 ----
        if confidence < CONFIDENCE_THRESHOLD:
            yield {
                "type": "handover",
                "data": {"reason": "low_confidence", "confidence": confidence},
            }
            return

        # ---- 3. 取业务 Agent 类 ----
        agent_cls = get_agent_class(agent_name)
        if agent_cls is None:
            logger.error(f"未注册的 Agent: {agent_name}")
            yield {"type": "error", "data": {"code": 5006, "message": "Agent 未注册"}}
            return

        # ---- 4. 实例化业务 Agent ----
        business_agent = agent_cls(user_id=user_id, session_id=session_id)

        # ---- 5. 上下文工程 ----
        enriched_query = await self.ctx_engineer.build(
            user_id=user_id,
            query=query,
            rag_namespace=business_agent.rag_namespace,
        )

        # ---- 6. 构建 SimpleAgent 并流式执行 ----
        simple_agent = business_agent.build()
        async for event in simple_agent.stream_run(enriched_query):
            yield event

        # ---- 7. 异步归档记忆 ----
        await self.ctx_engineer.persist_after_run(
            user_id=user_id,
            session_id=session_id,
            query=query,
            agent_name=agent_name,
            history=history,
        )
```

### 7.7 新增业务 Agent 的标准流程（SOP）

**目标**：新增业务 Agent 只改 Agent 类与登记表，不修改路由逻辑与 Orchestrator。
业务 Agent 放在 `app/agents/business/`，并在 `registry.py` 的 `_CODE_AGENTS` 中登记。

#### Step 1：创建业务 Agent 类

```bash
touch backend/app/agents/business/refund_agent.py
```

```python
from hello_agents.tools import ToolRegistry

from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.prompts.refund import REFUND_SYSTEM_PROMPT
from app.tools.custom.refund_tool import SubmitRefundTool
from app.tools.memory_factory import get_memory_tool


class RefundAgent(BaseBusinessAgent):
    name = "refund_service"          # 全局唯一，路由与 DB 均以它为标识
    description = "退款服务：申请退款、查询退款进度、退款政策咨询"
    system_prompt = REFUND_SYSTEM_PROMPT
    llm_model = "gpt-4o"
    rag_namespace = "cs_after_sales"

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(SubmitRefundTool())
```

#### Step 2：创建 Prompt 文件

```bash
touch backend/app/agents/prompts/refund.py
```

#### Step 3：开发业务专属工具（可选）

工具继承内核的 `app.tools.custom.base.BaseTool`，实现放在 `app/tools/custom/`：

```bash
touch backend/app/tools/custom/refund_tool.py
```

#### Step 4：登记 Agent 与工具

```python
# backend/app/agents/registry.py
from app.agents.business.refund_agent import RefundAgent

_CODE_AGENTS = (..., RefundAgent)

# backend/app/tools/custom_catalog.py
from app.tools.custom.refund_tool import SubmitRefundTool

_TOOL_CLASSES = (..., SubmitRefundTool)
```

注册表在进程启动时构建：Agent 必须是 `BaseBusinessAgent` 子类、工具必须是 `BaseTool` 子类，
name 全局唯一。

#### Step 5：重启后端

注册表在进程启动时构建，重启后生效。
如需在后台展示自定义名称或控制开关，可在 `agents` 表补一行（可选，不补时按默认启用处理）：

```sql
INSERT INTO agents (name, display_name, description, enabled)
VALUES ('refund_service', '退款服务', '申请退款、查询进度', true);
```

#### Step 6：测试

```python
# 注册表已生效（无需真模型）
from app.agents.registry import AGENT_REGISTRY


def test_refund_agent_registered():
    assert "refund_service" in AGENT_REGISTRY
```

#### Step 7：完成

路由 Agent 会自动在 prompt 中看到 `refund_service`（`RouterAgent._build_prompt()` 从
`AGENT_REGISTRY` + DB 动态 Agent 生成），无需改路由代码。

### 7.8 自定义工具（继承 BaseTool）

**文件：`backend/app/tools/custom/order_tool.py`**

```python
"""
自定义工具：继承 HelloAgents 的 BaseTool
"""
import httpx
from hello_agents.tools import BaseTool


class QueryOrderTool(BaseTool):
    name = "query_order"
    description = "根据订单号查询订单状态。仅用于订单相关查询。"
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号"}
        },
        "required": ["order_id"],
        "additionalProperties": False,
    }
    timeout = 10
    base_url = "http://order-service:8080"

    async def run(self, order_id: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}/orders/{order_id}")
            if resp.status_code == 404:
                return {"ok": False, "error": "订单不存在"}
            resp.raise_for_status()
            return {"ok": True, "data": resp.json()}
```

### 7.9 与旧版（v1.2）的差异对照

| 维度 | v1.2（旧） | v1.3（新） |
|------|-----------|-----------|
| 多 Agent 定义 | 一个 SimpleAgent + DB 元数据配置 | 每个业务独立 Python 类 |
| Prompt 管理 | DB `agents.system_prompt` 字段 | 代码 `prompts/{business}.py` |
| 工具装配 | DB `agents.tools` JSON 数组 | 每个类实现 `build_tools()` |
| 新增 Agent | DB 插记录 + 重启 | 新建类文件 + 注册表加一行 |
| 路由 | 隐藏在 IntentClassifier | 独立的 `RouterAgent` 类 |
| 路由 Agent | 无独立类 | 独立的 `router_agent.py`，专用轻量模型 |
| 业务 Agent 类结构 | 无 | `BaseBusinessAgent` 抽象基类 |
| DB `agents` 表 | 存业务配置 | 只存管理元数据（name/enabled） |
| 上下文工程 | 全局 ContextEngineer | 按业务 Agent 的 `rag_namespace` 定制 |

---

## 8. 记忆系统规范（基于 MemoryTool）

### 8.1 四层记忆定义

| 类型 | 存储位置 | TTL | 写入触发 | 检索参与 |
|------|---------|-----|---------|---------|
| working | MemoryTool 内部 | 30 min | 每轮对话 | 是（最高优先） |
| episodic | MemoryTool 内部 | 永久 | 重要事件 / 阈值触发 | 是 |
| semantic | MemoryTool 内部 | 永久 | 定时任务提炼 | 是 |
| perceptual | MemoryTool 内部 | 90 天 | 上传图片/语音时 | 否（默认） |

### 8.2 初始化（强制）

```python
from hello_agents.tools import MemoryTool

memory_tool = MemoryTool(
    user_id="customer_12345",
    memory_types=["working", "episodic", "semantic", "perceptual"],
)
```

### 8.3 支持的操作（`action` 取值）

| action | 参数 | 说明 |
|--------|------|------|
| `add` | `memory_type`, `content`, `metadata` | 添加记忆 |
| `search` | `query`, `limit`, `memory_type?` | 检索记忆 |
| `update` | `memory_uid`, `content`, `importance?` | 更新记忆 |
| `forget` | `memory_uid` | 遗忘单条记忆 |
| `clear` | `memory_type?` | 清空某类或全部记忆 |
| `list` | `memory_type?`, `limit` | 列出记忆 |

### 8.4 MemoryService 封装

**文件：`backend/app/services/memory_service.py`**

```python
from app.tools.memory_factory import get_memory_tool


class MemoryService:
    async def search(self, user_id: str, query: str, limit: int = 10):
        tool = get_memory_tool(user_id)
        return tool.run({
            "action": "search",
            "query": query,
            "limit": limit,
        })

    async def add(self, user_id: str, type_: str, content: str, metadata: dict):
        tool = get_memory_tool(user_id)
        return tool.run({
            "action": "add",
            "memory_type": type_,
            "content": content,
            "metadata": metadata,
        })

    async def forget(self, user_id: str, memory_uid: str):
        tool = get_memory_tool(user_id)
        return tool.run({
            "action": "forget",
            "memory_uid": memory_uid,
        })
```

### 8.5 记忆重要性评分算法

```python
def compute_importance(content: str, metadata: dict, signals: dict) -> float:
    """返回值范围 0~1，辅助业务侧决定是否写入长期记忆"""
    score = 0.3
    if any(k in metadata for k in ("order_id", "ticket_id", "phone")):
        score += 0.2
    score += min(signals.get("sentiment_intensity", 0), 1.0) * 0.2
    score += min(signals.get("turn_count", 0) / 10, 1.0) * 0.1
    if signals.get("repeat_mention", False):
        score += 0.2
    return min(score, 1.0)
```

### 8.6 短期 → 长期转化规则

满足以下任一条件时业务侧强制归档：

- `importance >= MEMORY_IMPORTANCE_THRESHOLD`（默认 0.6）
- `access_count >= MEMORY_ACCESS_THRESHOLD`（默认 3）
- 会话结束时（session.status = closed）
- 检测到关键实体（order_id、complaint 关键词）

---

## 9. RAG 系统规范（基于 RAGTool）

### 9.1 初始化（强制）

```python
from hello_agents.tools import RAGTool

rag_tool = RAGTool(
    knowledge_base_path="./data/knowledge_bases",
    rag_namespace="cs_order",
)
```

### 9.2 支持的操作（`action` 取值）

| action | 参数 | 说明 |
|--------|------|------|
| `add_document` | `file_path`, `metadata` | 添加文档 |
| `add_text` | `text`, `metadata` | 添加纯文本 |
| `search` | `query`, `top_k` | 检索 |
| `delete_document` | `document_id` | 删除文档 |
| `list_documents` | — | 列出文档 |
| `reindex` | — | 重建索引 |

### 9.3 文档处理流水线

```
上传 → RAGTool.add_document
     → 内部：格式识别 → 文本提取 → 清洗 → 分块 → 向量化 → 入库
```

**业务侧仅需调用：**

```python
result = rag_tool.run({
    "action": "add_document",
    "file_path": "/path/to/file.pdf",
    "metadata": {"title": "售后服务手册", "category": "after_sales"},
})
```

### 9.4 多 namespace 隔离策略

| namespace | 用途 | 使用业务 Agent |
|-----------|------|---------------|
| `cs_default` | 通用 FAQ、公司政策 | KnowledgeAgent |
| `cs_order` | 订单业务知识 | OrderAgent |
| `cs_after_sales` | 售后 / 退换货 | ComplaintAgent |
| `cs_internal` | 内部 SOP（仅客服） | — |

### 9.5 DocumentService

**文件：`backend/app/services/document_service.py`**

```python
"""
DocumentService：上传文档 → 调用 RAGTool.add_document → 归档 DB
"""
import shutil
from pathlib import Path
from app.tools.rag_factory import get_rag_tool, invalidate_rag_tool
from app.models.document import DocumentRepo


class DocumentService:
    async def upload(self, file, namespace: str, title: str, uploaded_by: str):
        tmp_path = Path(f"/tmp/{file.filename}")
        with tmp_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)

        doc = await DocumentRepo.create(
            title=title,
            source=str(tmp_path),
            content_type=file.content_type,
            rag_namespace=namespace,
            uploaded_by=uploaded_by,
            status="processing",
        )

        try:
            rag_tool = get_rag_tool(namespace)
            result = rag_tool.run({
                "action": "add_document",
                "file_path": str(tmp_path),
                "metadata": {"title": title, "doc_id": str(doc.id)},
            })
            await DocumentRepo.update_status(
                doc.id, "ready", chunk_count=result.get("chunk_count", 0)
            )
            invalidate_rag_tool(namespace)
        except Exception as e:
            await DocumentRepo.update_status(doc.id, "failed", error_message=str(e))
            raise
        finally:
            tmp_path.unlink(missing_ok=True)

        return doc
```

### 9.6 增量索引

- 文档变更 → `action=add_document` 覆盖式写入
- 删除文档 → `action=delete_document`
- **禁止**绕过 RAGTool 直接操作底层向量库

---

## 10. 流式输出规范

### 10.1 SSE 协议约束

- `Content-Type: text/event-stream; charset=utf-8`
- `Cache-Control: no-cache`
- `Connection: keep-alive`
- 事件格式：`event: <type>\ndata: <json>\n\n`
- 心跳：每 15 秒发送 `: ping\n\n`
- 客户端断开时捕获 `asyncio.CancelledError`

### 10.2 后端实现

```python
# api/v1/chat.py
from fastapi.responses import StreamingResponse
from app.services.chat_service import ChatService

@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, user=Depends(current_user)):
    service = ChatService()

    async def gen():
        try:
            async for chunk in service.stream(
                user_id=str(user.id),
                session_id=body.session_id,
                message=body.message,
            ):
                yield chunk
        except asyncio.CancelledError:
            logger.info("client disconnected")
            raise
        except Exception as e:
            yield sse_event("error", {"code": 5001, "message": str(e)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
```

### 10.3 ChatService

**文件：`backend/app/services/chat_service.py`**

```python
from typing import AsyncIterator
from app.agents.orchestrator import Orchestrator
from app.models.session import SessionRepo
from app.models.message import MessageRepo
from app.utils.sse import sse_event


class ChatService:
    def __init__(self):
        self.orchestrator = Orchestrator()

    async def stream(
        self,
        user_id: str,
        session_id: str | None,
        message: str,
    ) -> AsyncIterator[str]:
        session = await SessionRepo.get_or_create(user_id, session_id)
        await MessageRepo.add(session.id, role="user", content=message)

        yield sse_event("session", {"session_id": str(session.id)})

        history = await MessageRepo.list_by_session(session.id)

        assistant_content = ""
        routed_to = None
        async for event in self.orchestrator.dispatch(
            user_id=str(session.user_id),
            session_id=str(session.id),
            query=message,
            history=history,
        ):
            if event["type"] == "route":
                routed_to = event["data"].get("agent_name")
            if event["type"] == "text":
                assistant_content += event.get("content", "")
            yield sse_event(event["type"], event.get("data", event))

        msg = await MessageRepo.add(
            session.id,
            role="assistant",
            content=assistant_content,
            routed_to=routed_to,
        )

        yield sse_event("done", {"message_id": str(msg.id)})
```

### 10.4 前端接收规范

```typescript
export function streamChat(
  sessionId: string | null,
  message: string,
  handlers: Record<string, (data: any) => void>
): AbortController {
  const controller = new AbortController()
  fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify({ session_id: sessionId, message, stream: true }),
    signal: controller.signal,
  }).then(async (resp) => {
    const reader = resp.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const events = buffer.split('\n\n')
      buffer = events.pop() || ''
      for (const raw of events) {
        const { event, data } = parseSSE(raw)
        handlers[event]?.(data)
      }
    }
  }).catch((e) => { if (e.name !== 'AbortError') handlers.error?.(e) })
  return controller
}
```

### 10.5 首字延迟要求

| 指标 | 目标 |
|------|------|
| TTFT（首字时间） | < 500 ms（P95） |
| 完整回复 | < 5 s（P95） |
| 心跳间隔 | 15 s |

---

## 11. 前端规范

### 11.1 页面路由

| 路径 | 组件 | 权限 |
|------|------|------|
| `/login` | LoginView | 公开 |
| `/chat` | ChatView | customer/agent |
| `/history` | HistoryView | customer/agent |
| `/admin/knowledge` | DocumentList | admin |
| `/admin/memory/:userId?` | MemoryView | agent/admin |
| `/admin/agents` | AgentList | admin |
| `/admin/dashboard` | Dashboard | admin |

### 11.2 状态管理（Pinia）

```typescript
export const useChatStore = defineStore('chat', () => {
  const sessionId = ref<string | null>(null)
  const messages = ref<Message[]>([])
  const currentAgent = ref<string | null>(null)

  function appendText(text: string) {
    const last = messages.value[messages.value.length - 1]
    if (last?.streaming) last.content += text
    else messages.value.push({ id: nanoid(), role: 'assistant', content: text, streaming: true })
  }

  function setRoute(agentName: string) {
    currentAgent.value = agentName
  }

  function finishStreaming() {
    const last = messages.value[messages.value.length - 1]
    if (last) last.streaming = false
  }

  return { sessionId, messages, currentAgent, appendText, setRoute, finishStreaming }
})
```

### 11.3 流式渲染规范

- `markdown-it` + `highlight.js`
- 每收到 `text` 事件立即追加，不做节流
- `route` 事件时展示当前服务的业务 Agent 名称
- `tool_call` / `tool_result` 以卡片形式展示
- `done` 后启用"复制"、"重新生成"

### 11.4 UI 组件清单

| 组件 | 说明 |
|------|------|
| `MessageBubble` | 消息气泡 |
| `ToolCallCard` | 工具调用卡片 |
| `StreamRenderer` | Markdown 流式渲染 |
| `AgentBadge` | 展示当前业务 Agent |
| `MemoryTimeline` | 记忆时间线 |
| `DocumentUploader` | 拖拽上传 |

### 11.5 前端性能指标

| 指标 | 目标 |
|------|------|
| 首屏加载 | < 1.5 s |
| 路由切换 | < 200 ms |
| 流式首字渲染 | < 100 ms |

---

## 12. 鉴权与安全规范

### 12.0 双入口鉴权（人员/客户分表，[U07](updates/07-staff-customer-auth-split.md)）

`users` 已拆为 `staff_users`（`agent`/`admin`）与 `customer_users`（可自助注册），登录入口分离：

| 入口 | 端点 | 读表 | 说明 |
|------|------|------|------|
| 后台人员 | `POST /auth/staff/login` | `staff_users` | admin-frontend 使用 |
| 客户 | `POST /auth/customer/login` | `customer_users` | 账号不存在返回 `2004`，引导注册 |
| 客户自助注册 | `POST /auth/customer/register` | `customer_users` | 成功即下发 Token 自动登录 |

Token `sub`=用户ID、`role`∈`{customer,agent,admin}`；`/auth/refresh` 按 `role` 回查对应表。依赖注入：`current_customer`（客户端，仅本人）、`require_staff`（agent+admin）、`require_admin`。

### 12.1 JWT

- 算法：HS256
- Payload：`{ sub: user_id, role, exp, iat, jti }`
- Access Token TTL：7200 s
- Refresh Token TTL：604800 s

### 12.2 RBAC 权限矩阵

| 资源 | customer | agent | admin |
|------|----------|-------|-------|
| 自己的会话 | RW | - | - |
| 他人会话（跨客户咨询） | - | R | RW |
| 自己的记忆 | R | - | - |
| 他人记忆 | - | RW | RW |
| 知识库 / 文档 | - | R | RW |
| 工单（tickets） | - | R/W 流转 | R/W 流转 |
| 请求链路（traces） | - | R | R |
| Agent / MCP 管理（写） | - | - | RW |

### 12.3 输入输出安全

**Prompt Injection 检测：**

- 模式：`ignore (previous|above) instructions`、`system prompt` 等
- 命中返回 `code=4003`

**输出脱敏：**

```python
SENSITIVE_PATTERNS = [
    (r"\b1[3-9]\d{9}\b", "1**********"),
    (r"\b\d{17}[\dXx]\b", "******************"),
    (r"\b\d{16,19}\b", "**** **** **** ****"),
]
```

### 12.4 限流

| 维度 | 限制 |
|------|------|
| 单用户对话 | 20 次/分钟 |
| 单用户上传 | 10 次/小时 |
| 单 IP 登录 | 10 次/分钟 |
| 全局并发 SSE | 500 |

单主体限流参数走 `PUBLIC_CHAT_*` 环境变量（默认 30 次/分、2000 次/日、并发 3），Redis 故障 fail-open；本表是进程级兜底，与场景维度额度同时生效。

### 12.5 创建配额与场景额度（U21）

**登录人创建配额**：`staff_users.quota` JSONB，键 `max_scenes` / `max_agents` / `max_knowledge_bases`。

| 值 | 含义 |
|----|------|
| 键缺失 / `null` | 不限（新账号默认） |
| `0` | 禁止创建 |
| `> 0` | 上限 |

- 只统计 `created_by = 当前登录人`；`created_by IS NULL`（默认场景、seed 代码 Agent、演示数据）不计入任何人；
- 删除立即释放，编辑不消耗，归档场景仍占用（行还在）；
- 超限 `QuotaExceeded`（`3006` / 403），同时写 `audit_logs(action="quota.reject")`；
- 并发用进程内 `(user_id, kind)` 的 `asyncio.Lock` 串行化「校验 + 创建」，多进程部署接受极小超额窗口；
- 管理员不能改自己的配额与角色，后台「人员与配额」页只对 `staff_users` 生效（客户账号不受限）。

**场景请求额度**：`scenes.runtime_config.quota.per_minute` / `quota.daily`（范围 0–600 / 0–100000）。

- `0` / 空 = **不限**，且**不继承** `PUBLIC_CHAT_*`（后者限的是单主体，场景额度限的是整场景）；
- Redis 桶由 3 个扩到 5 个（主体分钟/日 + 场景分钟/日 + 主体并发流），仍是一次 pipeline 提交全部 `INCR`，在内存判定；任一超限就回滚**本次已自增的全部键**再抛 `4029`（回滚到 ≤0 时删键，避免跨分钟留下无 TTL 的负计数）；
- 后台场景列表的 `today_requests` / `quota_daily` / `quota_source` 由一条 `GROUP BY scene_code` 聚合，禁止逐场景 count。

---

## 13. 错误码规范

| code | HTTP | 说明 |
|------|------|------|
| 0 | 200 | 成功 |
| 1001 | 400 | 参数校验失败 |
| 2001 | 401 | 未认证 |
| 2002 | 401 | Token 过期 |
| 2003 | 403 | 无权限 |
| 2004 | 404 | 账号不存在，请先注册（客户登录引导） |
| 3001 | 404 | 资源不存在 |
| 3002 | 409 | 资源冲突（重名 / 仍被引用） |
| 3006 | 403 | 创建数量已达上限（登录人配额） |
| 4001 | 400 | 工具参数校验失败 |
| 4003 | 400 | 输入包含不安全内容 |
| 4029 | 429 | 请求过于频繁 |
| 5001 | 500 | 内部错误 |
| 5002 | 502 | LLM 服务不可用 |
| 5003 | 503 | RAGTool 检索失败 |
| 5004 | 504 | 工具调用超时 |
| 5005 | 500 | MemoryTool 调用失败 |
| 5006 | 500 | Agent 未注册 |

**错误响应格式：**

```json
{
  "code": 1001,
  "message": "参数校验失败",
  "data": { "field": "message", "reason": "长度不能为空" }
}
```

---

## 14. 日志与可观测性规范

### 14.1 日志格式（structlog JSON）

```json
{
  "ts": "2025-01-01T12:00:00.123Z",
  "level": "info",
  "logger": "app.agents.orchestrator",
  "msg": "agent dispatched",
  "trace_id": "abc123",
  "session_id": "uuid",
  "user_id": "uuid",
  "agent_name": "order_service",
  "confidence": 0.92
}
```

### 14.2 关键指标（Prometheus）

| 指标名 | 类型 | 标签 |
|--------|------|------|
| `cs_chat_requests_total` | Counter | status |
| `cs_chat_ttft_seconds` | Histogram | agent |
| `cs_router_decisions_total` | Counter | agent_name |
| `cs_router_confidence` | Histogram | — |
| `cs_tool_calls_total` | Counter | tool, status |
| `cs_memory_tool_calls_total` | Counter | action |
| `cs_rag_tool_calls_total` | Counter | action, namespace |
| `cs_llm_tokens_total` | Counter | model, type |
| `cs_active_sse_connections` | Gauge | — |

### 14.3 告警规则

| 规则 | 阈值 |
|------|------|
| 错误率 | > 5% (5 min) |
| P95 延迟 | > 5s (5 min) |
| LLM 失败率 | > 10% (5 min) |
| 工具超时率 | > 20% (5 min) |
| 路由低置信度比例 | > 30% (5 min) |
| SSE 连接数 | > 400 |

### 14.4 全链路追踪

- 每请求生成 `trace_id`
- 贯穿 API → Orchestrator → RouterAgent → 业务 Agent → 工具
- 响应头返回 `X-Trace-Id`

---

## 15. 测试规范

### 15.1 覆盖率要求

| 模块 | 最低覆盖率 |
|------|-----------|
| agents / tools（HelloAgents 封装 + 业务 Agent） | 80% |
| API 层 | 70% |
| 前端组件 | 60% |

### 15.2 HelloAgents 全栈集成测试

```python
# tests/integration/test_hello_agents_integration.py
import pytest
from hello_agents.llm import HelloAgentsLLM
from hello_agents.tools import MemoryTool, RAGTool, ToolRegistry
from hello_agents.agents import SimpleAgent


@pytest.mark.asyncio
async def test_full_hello_agents_stack():
    memory_tool = MemoryTool(
        user_id="test_user",
        memory_types=["working", "episodic", "semantic", "perceptual"],
    )
    rag_tool = RAGTool(
        knowledge_base_path="./tests/fixtures/kb",
        rag_namespace="test_ns",
    )

    registry = ToolRegistry()
    registry.register_tool(memory_tool)
    registry.register_tool(rag_tool)

    llm = HelloAgentsLLM()
    agent = SimpleAgent(
        name="测试助手",
        llm=llm,
        system_prompt="测试用智能助手",
        tool_registry=registry,
    )

    memory_tool.run({"action": "add", "memory_type": "episodic",
                     "content": "测试记忆", "metadata": {}})
    result = memory_tool.run({"action": "search", "query": "测试", "limit": 5})
    assert result is not None

    output = await agent.run("你好")
    assert output
```

### 15.3 多 Agent 路由测试（必测）

```python
# tests/agents/test_router.py
import pytest
from app.agents.router_agent import RouterAgent
from app.agents.registry import AGENT_REGISTRY


@pytest.mark.asyncio
@pytest.mark.parametrize("query, expected_agent", [
    ("我的订单到哪了", "order_service"),
    ("我要投诉", "complaint_handler"),
    ("退货政策是什么", "knowledge_qa"),
])
async def test_router(query, expected_agent):
    router = RouterAgent()
    result = await router.route(query)
    assert result["agent_name"] == expected_agent
    assert result["confidence"] > 0.5


def test_registry_completeness():
    from app.agents.business.knowledge_agent import KnowledgeAgent
    assert KnowledgeAgent.name in AGENT_REGISTRY
```

### 15.4 新增业务 Agent 的回归测试

```python
# tests/agents/test_new_agent_sop.py
def test_new_agent_auto_discovered():
    """新增 Agent 后，路由 prompt 应包含它"""
    from app.agents.router_agent import RouterAgent
    router = RouterAgent()
    prompt = router._build_prompt()
    for name, cls in AGENT_REGISTRY.items():
        assert name in prompt
        assert cls.description in prompt
```

### 15.5 E2E 测试用例

| 编号 | 场景 | 期望 |
|------|------|------|
| E2E-01 | 登录成功 | 返回 token |
| E2E-02 | 对话触发路由 | 收到 route 事件 |
| E2E-03 | 订单意图 | 路由到 order_service |
| E2E-04 | 对话触发工具调用 | tool_call / tool_result |
| E2E-05 | 上传文档 → RAGTool 检索 | 返回相关 chunk |
| E2E-06 | 查看 MemoryTool 记忆 | 时间线正确 |
| E2E-07 | 低置信度 → handover | handover 事件 |
| E2E-08 | 新增 Agent（代码注册） | 无需改路由即可命中 |

### 15.6 性能测试

- 工具：Locust
- 场景：100 并发 SSE 对话，持续 5 分钟
- 通过标准：错误率 < 1%，P95 TTFT < 800 ms

---

## 16. 验收标准

### 16.1 功能验收

| 编号 | 功能 | 验收标准 |
|------|------|---------|
| F-01 | 流式对话 | 首字 < 500ms，SSE 事件完整 |
| F-02 | MemoryTool 集成 | 四层记忆可写可查 |
| F-03 | RAGTool 集成 | 上传后 30s 内可检索 |
| F-04 | 路由 Agent | 意图路由准确率 ≥ 90% |
| F-05 | 业务 Agent 独立开发 | 每个业务独立类、独立 Prompt、独立工具 |
| F-06 | 新增 Agent 零改路由 | 注册表加一行即可命中 |
| F-07 | MCP 工具 | 至少 2 个 MCP Server |
| F-08 | 自定义工具 | 参数校验、超时、重试生效 |
| F-09 | 历史对话 | 分页加载 |
| F-10 | 知识库管理 | pdf/docx/md/txt |
| F-11 | 记忆查看 | 按用户、类型、重要性筛选 |
| F-12 | 人工接管 | 低置信度触发 handover |

### 16.2 非功能验收

| 编号 | 指标 | 标准 |
|------|------|------|
| N-01 | TTFT P95 | < 500 ms |
| N-02 | 对话 P95 | < 5 s |
| N-03 | 并发 SSE | ≥ 500 |
| N-04 | 错误率 | < 1% |
| N-05 | 单元测试覆盖率 | 核心 ≥ 80% |
| N-06 | 安全 | 无越权、无注入、无敏感泄露 |

### 16.3 交付物清单

- [ ] 后端代码 + 迁移脚本 + 单元测试
- [ ] 前端代码 + E2E 测试
- [ ] 至少 3 个独立业务 Agent（knowledge / order / complaint）
- [ ] 路由 Agent + 注册表 + Orchestrator
- [ ] API 文档（Swagger 自动生成）
- [ ] 运维手册（`docs/runbook.md`）
- [ ] 性能测试报告
- [ ] 安全测试报告

---

## 17. 附录

### 17.1 HelloAgents 使用速查

```python
# ============ 1. LLM ============
from hello_agents.llm import HelloAgentsLLM
llm = HelloAgentsLLM()  # 读取 LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL

# ============ 2. 工具 ============
from hello_agents.tools import MemoryTool, RAGTool, ToolRegistry, MCPTool, BaseTool

memory_tool = MemoryTool(
    user_id="customer_123",
    memory_types=["working", "episodic", "semantic", "perceptual"],
)

rag_tool = RAGTool(
    knowledge_base_path="./data/knowledge_bases",
    rag_namespace="cs_default",
)

registry = ToolRegistry()
registry.register_tool(memory_tool)
registry.register_tool(rag_tool)

# ============ 3. Agent ============
from hello_agents.agents import SimpleAgent
agent = SimpleAgent(
    name="智能客服助手",
    llm=llm,
    system_prompt="你是一个智能客服助手",
    tool_registry=registry,
)

# ============ 4. 执行 ============
result = await agent.run("用户问题")
async for event in agent.stream_run("用户问题"):
    ...
```

### 17.2 核心 API 速查表

> 本表仅为概览；**完整、准确的端点清单以 [`api.md`](api.md) 为准**。

```
POST   /api/v1/auth/staff/login        # 后台人员登录
POST   /api/v1/auth/customer/login     # 客户登录（不存在→2004）
POST   /api/v1/auth/customer/register  # 客户自助注册
POST   /api/v1/auth/refresh
GET    /api/v1/auth/me

POST   /api/v1/chat/stream           # SSE
POST   /api/v1/chat/message

GET    /api/v1/sessions
GET    /api/v1/sessions/{id}/messages

GET    /api/v1/memory/me
GET    /api/v1/memory/users/{user_id}
POST   /api/v1/memory/search

# 知识库：命名空间 CRUD / 文档 / 手工条目 / 分块 / 检索
GET    /api/v1/knowledge/bases
POST   /api/v1/knowledge/bases
POST   /api/v1/knowledge/documents
GET    /api/v1/knowledge/documents/{id}/raw
POST   /api/v1/knowledge/entries
GET    /api/v1/knowledge/chunks
POST   /api/v1/knowledge/search

# Agent（代码+动态混合）/ MCP
GET    /api/v1/admin/agents
POST   /api/v1/admin/agents               # 创建动态 Agent
PUT    /api/v1/admin/agents/{name}
DELETE /api/v1/admin/agents/{name}
PATCH  /api/v1/admin/agents/{name}/toggle
GET    /api/v1/admin/mcp-servers
POST   /api/v1/admin/mcp-servers
POST   /api/v1/admin/mcp-servers/test
GET    /api/v1/admin/tools
GET    /api/v1/admin/metrics

# 工单 / 跨客户咨询 / 链路
GET    /api/v1/admin/tickets
PATCH  /api/v1/admin/tickets/{ticket_no}/status
GET    /api/v1/admin/inquiries/sessions
GET    /api/v1/admin/traces
GET    /api/v1/admin/traces/{trace_ref}

GET    /health
GET    /metrics
```

### 17.3 SSE 事件类型全表

| event | 触发时机 | 必带字段 |
|-------|---------|---------|
| `session` | 流开始 | session_id |
| `route` | 路由完成 | agent_name, confidence, reason, intents, intent_total |
| `agent_start` | 意图分支开始 | agent_name, intent_index, intent_total, confidence, reason |
| `agent_end` | 意图分支结束 | agent_name, intent_index, intent_total |
| `text` | 文本增量 | content |
| `tool_call` | 工具调用开始 | id, name, args |
| `tool_result` | 工具返回 | id, name, result, error |
| `memory_write` | 记忆写入 | type, id |
| `handover` | 转人工 | reason, confidence |
| `done` | 流结束 | message_id, trace_id |
| `error` | 异常 | code, message |

### 17.4 核心组件清单

| 组件 | 文件 | 职责 |
|------|------|------|
| `HelloAgentsBootstrap` | `core/bootstrap.py` | 启动初始化 |
| `BaseBusinessAgent` | `agents/base_business_agent.py` | 业务 Agent 抽象基类 |
| `RouterAgent` | `agents/router_agent.py` | 路由 Agent（独立） |
| `AGENT_REGISTRY` | `agents/registry.py` | 类注册表 |
| `Orchestrator` | `agents/orchestrator.py` | 调度器 |
| `KnowledgeAgent` | `agents/business/knowledge_agent.py` | 业务1 |
| `OrderAgent` | `agents/business/order_agent.py` | 业务2 |
| `ComplaintAgent` | `agents/business/complaint_agent.py` | 业务3 |
| `ContextEngineer` | `core/context_engineer.py` | 上下文融合 |
| `MemoryFactory` | `tools/memory_factory.py` | MemoryTool 工厂 |
| `RagFactory` | `tools/rag_factory.py` | RAGTool 工厂 |
| `MCPLoader` | `tools/mcp_loader.py` | MCP 工具加载 |
| `ChatService` | `services/chat_service.py` | SSE 输出 |

### 17.5 开发任务看板

**Epic 1：HelloAgents 集成基础**
- [ ] T1.1 安装 hello-agents 依赖
- [ ] T1.2 实现 `HelloAgentsBootstrap`
- [ ] T1.3 FastAPI lifespan 挂载
- [ ] T1.4 环境变量接入
- [ ] T1.5 `validate_agents_sync` 启动校验
- [ ] T1.6 HelloAgents 集成测试通过

**Epic 2：MemoryTool 封装**
- [ ] T2.1 `MemoryFactory.get_memory_tool(user_id)` + TTL 缓存
- [ ] T2.2 `MemoryService` 封装
- [ ] T2.3 记忆归档到 DB（mirror）
- [ ] T2.4 记忆查看 / 修正 API
- [ ] T2.5 单元测试

**Epic 3：RAGTool 封装**
- [ ] T3.1 `RagFactory.get_rag_tool(namespace)` + TTL 缓存
- [ ] T3.2 `DocumentService` 调用 `RAGTool.add_document`
- [ ] T3.3 `RagService` 调用 `RAGTool.search`
- [ ] T3.4 namespace 多域隔离
- [ ] T3.5 单元测试

**Epic 4：多 Agent 架构**
- [ ] T4.1 `BaseBusinessAgent` 抽象基类
- [ ] T4.2 `RouterAgent` 独立路由 Agent
- [ ] T4.3 `AGENT_REGISTRY` 类注册表
- [ ] T4.4 `Orchestrator` 调度器（含 route 事件）
- [ ] T4.5 `KnowledgeAgent` 业务 Agent 1
- [ ] T4.6 `OrderAgent` 业务 Agent 2
- [ ] T4.7 `ComplaintAgent` 业务 Agent 3
- [ ] T4.8 每个业务 Prompt 独立文件
- [ ] T4.9 新增 Agent SOP 验证（RefundAgent）
- [ ] T4.10 路由准确率测试

**Epic 5：工具系统**
- [ ] T5.1 `BaseTool` 自定义工具（query_order / create_ticket 等）
- [ ] T5.2 `MCPLoader` 加载 MCPTool
- [ ] T5.3 工具审计日志
- [ ] T5.4 工具管理 API

**Epic 6：ContextEngineer**
- [ ] T6.1 并行调用 MemoryTool + RAGTool
- [ ] T6.2 单源失败降级
- [ ] T6.3 Token 预算分配
- [ ] T6.4 会话后异步持久化

**Epic 7：流式 API**
- [ ] T7.1 SSE 端点
- [ ] T7.2 ChatService 对接 Orchestrator
- [ ] T7.3 心跳与断连处理
- [ ] T7.4 TTFT 优化

**Epic 8：前端**
- [ ] T8.1 项目初始化 + 登录页
- [ ] T8.2 流式对话视图
- [ ] T8.3 AgentBadge 组件
- [ ] T8.4 会话列表 + 历史消息
- [ ] T8.5 知识库管理
- [ ] T8.6 记忆查看
- [ ] T8.7 Agent 管理（含启用/禁用）

**Epic 9：测试**
- [ ] T9.1 单元测试（MemoryTool / RAGTool / 业务 Agent 封装）
- [ ] T9.2 集成测试（HelloAgents 全栈）
- [ ] T9.3 路由准确率测试
- [ ] T9.4 E2E 测试
- [ ] T9.5 性能测试
- [ ] T9.6 安全测试

### 17.6 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2025-01-XX | 初版 |
| v1.1 | 2025-01-XX | 移除 Docker 与部署章节 |
| v1.2 | 2025-01-XX | Agent / MemoryTool / RAGTool 基于 HelloAgents |
| v1.3 | 2025-01-XX | 多 Agent 架构重写：业务独立开发 + 路由 Agent 分发 |

---

> **本 Spec 为可执行规范**：所有开发者必须严格遵循上述接口契约与 HelloAgents 框架 API 使用方式。任何偏离需提交 RFC 并经 Tech Lead 审批。
>
> **执行方式**：将本文件作为唯一真相源（SSOT），配套 17.5 节任务看板逐项落地，每完成一项在验收标准（第 16 节）中标记。

---

**文档结束**