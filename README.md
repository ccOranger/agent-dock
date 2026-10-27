# AgentDock

[![CI](https://github.com/ccOranger/smart-cs/actions/workflows/ci.yml/badge.svg)](https://github.com/ccOranger/smart-cs/actions/workflows/ci.yml)

> **可私有化部署的对话式 Agent 运营平台。** 一套代码、一个聊天入口、多业务场景：后台建场景、绑 Agent 与知识库，再用一行 script 嵌入第三方页面；客服/售后只是首个内置业务，不是产品边界。

仓库代号仍为 `smart-cs`：Compose 项目名、Python 包名与目录结构保持不变，产品对外名称统一为 **AgentDock**。

> 当前为 v0.1 早期版本，接口与表结构仍可能调整；升级前请先备份。

![多意图对话：一条消息拆两个意图、工具回填真实物流、投诉自动建单、最后转人工](docs/assets/screenshots/01-chat-multi-intent.gif)

| 运营台 · 链路详情 | 运营台 · 业务场景 |
|---|---|
| ![链路详情：路由 raw、意图依据、工具调用与分段耗时](docs/assets/screenshots/02-admin-traces.png) | ![业务场景：默认场景直连，客服场景按 LLM 路由](docs/assets/screenshots/03-admin-scenes.png) |

> 截图均为 `SEED_DEMO=true` 的演示数据；完整取景清单与录制参数见 [演示脚本](docs/demo-script.md)。

## 它解决什么问题

多数"智能客服"项目停在"文档问答 + 一个大模型提示词"：一条消息里有两个诉求只能答一个，追问下一句就忘了上文，工具调用看不见执行过程，出了问题也说不清是哪一步。

AgentDock 把这些做成平台能力，而不是某个业务里的临时代码：

| 能力 | 说明 | 关键开关 |
|---|---|---|
| **业务场景** | 一个 `/chat/stream` 用 `scene_code` 区分场景；每个场景有独立 Agent 池、路由档案、话术、知识库范围、会话与记忆；无码请求落默认场景 | 运营台「业务场景」 |
| **嵌入 / API 接入** | 一行 script 气泡组件（iframe 隔离宿主样式）；公开配置 + 访客签发；客户 Token / API key / 发布标识 / 已发布匿名四种鉴权 | 场景「接入」、`/public/scenes/*` |
| **多意图路由** | 一条消息拆成多个意图，按主次串行应答；追问自动续接上一轮 Agent；"不贴合任何 Agent"时可先由兜底 Agent 应答 | `ROUTER_MAX_INTENTS` / `ROUTER_CONFIDENCE_THRESHOLD` / `ROUTER_FALLBACK_ANSWER` |
| **混合 Agent 注册** | 代码托管 Agent 与后台动态 Agent 同时注册；新增/停用实时进入路由候选，无需改路由代码 | 运营台「Agent 管理」 |
| **路由线索** | 每个 Agent 声明触发关键词与典型问法，路由提示词去具名化，不偏向任何内置 Agent | `routing_keywords` / `routing_examples` |
| **知识与 RAG** | 多命名空间、文档上传/分块浏览/重建索引/原文下载、检索测试；向量存 Qdrant | `RAG_*` / `RAG_DEFAULT_NAMESPACE` |
| **工具调用** | 代码自定义工具 + MCP（stdio / SSE / streamable HTTP），异步执行并记录结果 | 运营台「自定义工具」「MCP 服务」 |
| **跨轮记忆** | working / episodic / perceptual 三层开箱可用；semantic 需 Neo4j；工单可跨轮补录 | `MEMORY_*` / `MEMORY_TYPES` |
| **上下文工程** | 按系统提示、历史、记忆、知识分配 token 预算，超限按优先级裁剪 | `CONTEXT_*` |
| **全链路追踪** | 每轮对话的路由、LLM、工具、RAG、记忆调用都有 spans 与耗时；可采样、可设保留期 | `TRACE_*` |
| **用量计量** | 每轮 token 与日聚合（按日 × 场景 × 接入方）与链路采样解耦，后台报表 + CSV 导出可当账目依据 | `TOKEN_ESTIMATE_FACTOR` / `USAGE_QUERY_MAX_DAYS` |
| **版本分层** | 离线 Ed25519 授权文件（不联网）；任何校验失败只降级不阻断，社区版保留完整对话能力 | `LICENSE_FILE` / `LICENSE_PUBLIC_KEY` / `DEPLOYMENT_ID` |
| **评测与回归** | 用例集进 Git，走真实 HTTP 链路跑意图/工具/RAG/兜底/转人工/P95/token 七类指标，与基线逐项 diff，退化即退出码 2 | `scripts/eval.py`、[评测与回归](docs/evaluation.md) |
| **固定话术** | 高频确定性问题在路由与大模型之前逐字命中，后台可测命中、可停用 | 运营台「固定话术」 |
| **双端身份** | 后台人员与客户分表、分登录入口、Token 隔离；客户可自助注册 | `/auth/staff/*`、`/auth/customer/*` |
| **运营台** | 咨询、工单、链路、知识库、Agent、路由分发器、MCP、工具、话术、记忆、仪表盘、用量报表、授权与版本 | `http://<host>:8080/admin/` |

## 架构

```mermaid
flowchart LR
    C[客户对话端] --> N[Nginx / Caddy]
    W[第三方页面 / 嵌入组件] --> N
    A[运营管理台] --> N
    N -->|/api| API[FastAPI]
    API --> S{场景解析 scene_code}
    S -->|默认场景| D[default_assistant 直连]
    S -->|业务场景| R[场景路由档案]
    R --> O[Orchestrator] --> B[场景内业务 Agent]
    B --> PG[(PostgreSQL)]
    B --> Q[(Qdrant)]
    B --> RD[(Redis)]
    B --> T[自定义工具 / MCP 服务]
    API -. 链路 spans .-> PG
```

一轮消息的处理顺序：

1. 先解析 `scene_code`：缺省落默认场景（直连），未知/停用返回 3003/3004；
2. 固定话术命中则直接逐字下发（不进入路由，也不调用大模型）；
3. 场景内 1 个 Agent 直连，2 个及以上由 `RouterAgent` 输出按主次排序的意图数组；低置信主意图整轮转人工；
4. `Orchestrator` 串行调度各业务 Agent：上下文工程 → 记忆召回 → RAG 检索 → 工具调用 → 流式应答；
5. 每个环节写入 `request_traces.spans`，SSE 的 `done` 事件带上 `trace_id`，可在运营台直达本轮链路。

## 快速开始（Docker Compose）

前置：Docker Engine / Docker Desktop + Compose **v2.24.4+**，本机无需 Python / Node；建议 4 核 8 GB 以上（首次构建约 5–8 分钟）。

```bash
cp backend/.env.example .env      # Windows: copy backend\.env.example .env
```

编辑 `.env`，至少填写：

```ini
# 对话模型（OpenAI 兼容端点）
LLM_MODEL_ID=deepseek-chat
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1

# 向量模型（记忆与 RAG）
EMBED_MODEL_TYPE=dashscope
EMBED_MODEL_NAME=text-embedding-v2
EMBED_API_KEY=sk-xxx
EMBED_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 鉴权（必填，否则 Compose 拒绝启动）
JWT_SECRET=<openssl rand -base64 48 的输出>
```

```bash
docker compose up -d --build
docker compose ps                 # postgres / qdrant / redis / backend / web 均应为 healthy
```

| 入口 | 地址 |
|---|---|
| 客户对话端 | http://localhost:8080/ |
| 管理后台 | http://localhost:8080/admin/ |
| 后端调试（仅宿主机回环） | http://localhost:8000/docs |

首次启动会自动建表并写入种子数据。`SEED_DEMO=false`（默认）时只创建一个管理员，**随机密码在 backend 首次启动日志中打印一次**：

```bash
docker compose logs backend | grep -i "密码"     # Windows: docker compose logs backend | Select-String "密码"
```

也可以先在 `.env` 里用 `SEED_ADMIN_USERNAME` / `SEED_ADMIN_PASSWORD` 指定初始管理员。

验证三件事：后台能登录 → 客户问一句业务问题能看到流式回复 → 运营台「链路追踪」能看到这一轮的 spans。

> 只想本地演示？在首次 `docker compose up` 前把 `SEED_DEMO=true` 写入 `.env`。演示账号为 `admin / Admin@123`、`service01 / Agent@123`、`alice / Alice@123`，**切勿用于生产**。

更细的步骤与常见报错见 [快速开始](docs/quickstart.md)。

## 接入一个新业务（不改代码）

1. 运营台「知识库」建库并上传文档；
2. 「Agent 管理」建动态 Agent：写 `system_prompt`、路由关键词/典型问法，按需绑定 MCP 或自定义工具；
3. 「业务场景」新建场景，绑定 Agent（顺序即候选顺序）、归属知识库；
4. 点该行「接入」：设置气泡标题/欢迎语/主题色与允许来源，打开「开放匿名」，复制嵌入代码。

```html
<script src="https://your-host/widget.js" data-scene="your_scene"></script>
```

没有 `scene_code` 的请求落默认场景（`default_assistant` 直连）。只用 API 对接时走
`POST /api/v1/chat/stream`，场景配置与访客签发走 `/api/v1/public/scenes/{code}`，详见 [API 速查](docs/api.md)。

场景的设计取舍、隔离边界与里程碑见 [多场景开发文档](docs/multi-scene-development.md)。

## 扩展业务能力

业务 Agent 与自定义工具都是普通代码资产，目录如下：

```
backend/app/agents/business/   # 代码 Agent（继承 BaseBusinessAgent）
backend/app/agents/prompts/    # 业务提示词
backend/app/tools/custom/      # 自定义工具（继承 BaseTool）
```

新增后分别在登记表补一行——`app/agents/registry.py` 的 `_CODE_AGENTS`、
`app/tools/custom_catalog.py` 的 `_TOOL_CLASSES`——重启后端生效。随后可在后台「业务场景」
把 Agent 绑定到场景；动态 Agent 可直接勾选已登记的自定义工具。运行时隔离与多业务接入由「业务场景」负责。

## 生产部署

单机生产在基础编排上叠加 `docker-compose.prod.yml`：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

它提供 Caddy 自动 HTTPS、固定数据目录（`DATA_ROOT`）、资源与日志限制、Redis/Qdrant 鉴权、一次性初始化（`INIT_ON_START=false`）。

```bash
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$COMPOSE up -d --build postgres qdrant redis
$COMPOSE run --rm backend python ../scripts/init_db.py
$COMPOSE run --rm backend python ../scripts/seed_data.py
$COMPOSE up -d --build
```

备份（PostgreSQL + Qdrant 快照 + 业务数据 + Redis RDB，带保留期）：

```bash
COMPOSE_FILE="docker-compose.yml:docker-compose.prod.yml" bash scripts/backup.sh
```

完整的 HTTPS、升级、恢复与安全清单见 [部署指南](docs/deployment.md) 与 [运维手册](docs/runbook.md)。

## 本地开发

```bash
# 后端
cd backend
uv venv --python 3.12
uv pip install -r requirements-dev.txt --python .venv/Scripts/python.exe
cp .env.example .env             # 本地库/Redis/Qdrant 连接串
python ../scripts/init_db.py     # 建表（幂等）
python ../scripts/seed_data.py   # 种子（幂等；全新库可用 ../scripts/rebuild_and_seed.py）
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 前端（仓库根，npm workspaces）
npm ci
npm run dev:chat                 # http://localhost:5173
npm run dev:admin                # http://localhost:5174
```

请用 `localhost` 而不是 `127.0.0.1` 访问 Vite 开发服务器（可能只监听 IPv6）。开发环境、测试命令与提交规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 目录结构

```
backend/          FastAPI + HelloAgents（app/{api,core,agents,tools,models,services}）
frontend/         客户对话端（Vue 3）
admin-frontend/   运营管理台（Vue 3）
packages/shared/  npm workspaces 共享包 @cs/shared（http / auth / SSE / 类型）
docker/           镜像、入口脚本、Nginx 与 Caddy 配置
scripts/          init_db / seed_data / rebuild_and_seed / migrate / eval / backup / cleanup_traces
docs/             快速开始、配置、部署、评测、FAQ、运维手册、规范与更新记录
```

## 文档

| 文档 | 内容 |
|---|---|
| [快速开始](docs/quickstart.md) | 从零到首次对话，含常见报错 |
| [配置说明](docs/configuration.md) | `.env` 全量配置、默认值与生效优先级 |
| [部署指南](docs/deployment.md) | Compose、生产叠加、HTTPS、备份恢复 |
| [常见问题](docs/faq.md) | 模型接入、Qdrant、MCP、Windows 编码、端口冲突 |
| [运维手册](docs/runbook.md) | 服务清单、排障、数据维护、发布前评测回归、安全 |
| [评测与回归](docs/evaluation.md) | 用例集、指标口径、基线对比与退化判定、报告与退出码 |
| [多场景开发](docs/multi-scene-development.md) | 场景/路由档案/隔离/公开接入/嵌入组件的设计与落地记录 |
| [API 速查](docs/api.md) | 接口、SSE 事件与错误码 |
| [技术文章](docs/articles/01-multi-intent-routing.md) | 多意图路由与提示词去具名化 |
| [开发规范](docs/spec.md) | Spec v2.0，与实现同步 |
| [功能更新记录](docs/updates/README.md) | U01–U24 每项能力的落地细节 |

## 路线图

| 阶段 | 状态 | 内容 |
|---|---|---|
| M0–M4 | 已完成 | 工程基线、业务代码分层、前端单仓化、一键部署、IT Helpdesk |
| 多场景 M0–M7 | 已完成 | 场景与路由档案、五类隔离、公开接入与限流、嵌入组件、重建验证 |
| v0.2 | 进行中 | 发布物料、版本化迁移（已完成）、用量计量（已完成）、License 与版本分层（已完成）、评测与回归（已完成）、RBAC/SSO |

## 明确不做（12 个月内）

- 通用 DAG 可视化编排与插件市场；
- 计费系统与自助订阅；
- 多租户 SaaS 隔离（当前单实例单租户，仅预留 `tenant_id`）；
- 移动端 App、自研模型/向量库/工单全流程；
- 为单个客户 fork 代码（只接受配置、场景与 Agent/工具定制）。

## 贡献与安全

- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)
- 产品定位见 [定位文档](docs/positioning.md)

## 许可证

Apache License 2.0，详见 [LICENSE](LICENSE)。
