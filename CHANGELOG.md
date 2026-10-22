# 更新日志（CHANGELOG）

本文件记录 AgentDock（仓库代号 `smart-cs`）的所有重要变更。

- 格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
- **版本编号说明**：开发期仓库内部使用 `1.x` 迭代号；自首个对外版本起统一为 `0.1.0` 并以此递增。
  `package.json`、`backend/pyproject.toml`、`backend/app/main.py` 与 `frontend/admin-frontend/packages/shared` 的
  `version` 必须与本文件顶部「最新版本」一致，`python scripts/release.py check` 会强制校验。
- 每项能力的落地细节（背景、契约、文件、验证、边界）见 [docs/updates/](docs/updates/README.md)，本文件只做面向使用者的摘要。

## [Unreleased]

### 新增：多场景平台化（M0–M7）

- **业务场景**：`scenes` + `scene_agents`；对外仍是 `/chat/stream`，用 `scene_code` 区分场景，无码请求落默认场景（固定 `default_assistant` 直连，不路由）。
- **场景隔离**：话术、知识库、会话、记忆、链路全部带场景维度。话术 `scene_id NULL` 为全局，同 key 场景行覆盖全局；知识库 `scene_id NULL` 为平台共享，`search_all` 只覆盖本场景归属库；跨场景复用 `session_id` 返回 404。
- **场景路由档案**：由场景配置 + Agent 池 + 全局模板派生的只读视图（direct / llm / unconfigured、候选、警告、`prompt_hash`），后台可查看详情、提示词并试运行；路由不是独立实体。
- **公开接入**：`GET /public/scenes/{code}` 下发展示配置与鉴权方式，`POST /public/scenes/{code}/visitors` 签发匿名访客；聊天鉴权支持客户 Token / `X-API-Key`（接入方 Key）/ `X-Publish-Key` / 已发布匿名四种方式。
- **动态 CORS**：按场景 `allowed_origins` 校验跨域（全局 `ALLOWED_ORIGINS` 继续放行自有前端）；场景码从路径、`?scene_code=` 或 `X-Scene-Code` 解析，preflight 未授权 403/2003，带 TTL 缓存与主动失效。
- **限流**：按 `scene + user/visitor/ip` 分桶的分钟频次 / 日配额 / 并发流（默认 30 / 2000 / 3），Redis 故障 fail-open，SSE 结束释放并发槽。
- **嵌入组件**：`embed.html` 独立 Vite 入口（不引入 Element Plus）+ `public/widget.js` 一行 script；气泡 + iframe 隔离宿主样式，主题/标题/欢迎语由后台下发，移动端全屏，postMessage 驱动关闭与未读角标。
- **后台场景化**：场景管理、接入抽屉（外观、允许来源、匿名开关、嵌入代码、实时预览）、路由档案页；话术 / 知识库 / 链路 / 咨询列表均增加场景维度；服务端 Key 统一在「接入方管理」签发与吊销。
- **初始化**：`scripts/init_db.sql` 作为唯一 DDL 源；`rebuild_and_seed.py` 支持 `SEED_DEMO=false`（生产初始化，随机管理员密码）与 `true`（演示数据）两种模式；不做旧库迁移，重建后校验行数。
- **演示种子场景**：`SEED_DEMO=true` 时额外创建 `customer_service` 客服场景（LLM 路由到投诉处理 / 通用知识问答 / 订单服务三个动态 Agent），并创建演示接入方（公开标识 `ic_demo_customer_service`）授权该场景，便于验证匿名归属。

### 新增：接入方与 Key 归属（U18）

- **接入方实体**：新增 `integrations` / `integration_keys` / `integration_scenes`，服务端 Key 从场景迁到接入方：一个接入方可持多把 Key（轮换期新旧并存）并被授权到多个场景，可独立轮换/吊销而不影响其他调用方。
- **鉴权语义**：`X-API-Key` 按 `key_hash` 反查启用中的接入方，Key 无效/接入方停用返回 401（不降级为匿名），Key 有效但目标场景未授权返回 403。
- **访客命名空间**：`visitors` 增加 `integration_id`，按「接入方 + 场景 + `external_key`」隔离；不同接入方传入相同客户标识不再串会话与记忆（两个 PostgreSQL 部分唯一索引避开 NULL）。
- **后台与接口**：新增 `/api/v1/admin/integrations` CRUD 与 Key 签发/吊销接口、后台「接入方管理」页；更新时未传 `scene_ids` 保持原授权，有访客的接入方禁止删除（409）。

### 新增：匿名接入的接入方归属（U19）

- **公开标识**：`integrations` 增加 `public_code`（`ic_xxx`，唯一），后台「接入方管理」展示并可复制；它是公开归属标识，不是密钥。
- **匿名归属**：嵌入组件新增 `data-integration`，公开配置、访客签发与聊天请求统一带 `X-Integration-Code`（公开配置也支持 `?integration=`）；带标识签发的匿名访客写入 `integration_id`，同一场景下不同接入方互不干扰。
- **拒绝语义**：公开标识无效 401、接入方未授权场景 403、场景未开放匿名 401，不降级为匿名池；登录客户优先，带客户 token 时不解析公开标识。
- **后台接入抽屉**：场景接入配置增加「归属接入方」选择，生成的嵌入代码与实时预览自动带 `data-integration`。
- **统计基础**：匿名访客可按 `integration_id` 归属，后续可通过 `request_traces.visitor_id → visitors.integration_id` 聚合各接入方的 LLM token 使用。

### 变更：单租户降级与自定义埋点（U20）

- **单租户降级**：后台隐藏「接入方管理」菜单与场景接入抽屉的「归属接入方 / 服务端 Key」，生成的嵌入代码不再带 `data-integration`；演示种子改为唯一「默认接入方」（`ic_default`）并授权全部场景，新建场景自动授权所有启用接入方，已签发的 Key 继续可用。入口 `/integrations` 与后端接口保留，改回 `SHOW_INTEGRATION_UI` 即可恢复。
- **自定义埋点入参**：`ChatRequest.ext` 接收扁平 JSON（≤10 键、仅标量、≤1KB、敏感键拒绝），原样存入 `chat_events.ext`；嵌入组件支持 `data-ext`，`widget.js` 透传到 iframe。渠道等设备信息不单建列，作为 `ext` 的普通键（如 `platform`）传入。
- **埋点表**：新增 `chat_events`（全量、不受 trace 采样影响；预留 `tenant_id` / `total_tokens`，不存消息正文与 IP），对话入口在鉴权后、限流前旁路写入，失败只告警；进入 service 拿到真实会话后按 `trace_id` 回填 `session_id` / `visitor_id`，首轮与匿名请求也能按会话/访客聚合。
- ⚠️ `SCHEMA_VERSION` 由 `4` 升到 `5`（新增 `chat_events`），旧库需执行 `scripts/rebuild_and_seed.py` 全量重建。

### 移除：场景包机制

- 删除 `manifest.yaml`、`app/packs/loader.py`、`/api/v1/admin/packs` 与运营台「场景包」页；导航不再按 `features` 显隐（工单入口固定可见）。
- 客服/IT 代码资产迁回 `app/agents/business/`、`app/agents/prompts/`、`app/tools/custom/`，由 `registry.py` / `custom_catalog.py` 显式登记，能力与工具名保持不变。

### 变更

- 默认场景只绑定 `default_assistant`，不再挂全部代码 Agent；业务 Agent 通过场景绑定进入候选。
- 旧路径 `GET /admin/router` 与 `/admin/router/prompt` 保留为全局视图别名，计划 v0.3 移除。
- 两个前端 vite 支持 `VITE_API_TARGET`（默认 `http://localhost:8000`），嵌入地址可用 `VITE_EMBED_URL` 覆盖（默认复用 `VITE_PUBLIC_CHAT_URL`）。
- ⚠️ 场景级服务端 Key 下线：删除 `scenes.api_key_hash` 与 `POST /api/v1/admin/scenes/{id}/rotate-key`，场景详情不再回显 `api_key_set`；旧 Key 无法自动映射到接入方，重建后需在「接入方管理」重新签发。
- ⚠️ `SCHEMA_VERSION` 由 `2` 升到 `3`（新增三张表、`visitors.integration_id` 与两个部分唯一索引），旧库需执行 `scripts/rebuild_and_seed.py` 全量重建。
- ⚠️ `SCHEMA_VERSION` 由 `3` 升到 `4`（`integrations.public_code` 与唯一约束），旧库同样需全量重建。

### 修复

- `publish_key` 含非 ASCII 字符时 `hmac.compare_digest` 抛 TypeError → 统一 UTF-8 编码后比较。

面向后续版本的候选项（只记录，不影响当前发布）：

- 版本化数据库迁移（Alembic 或 `scripts/migrations/NNN_*.sql` + `schema_version`）；
- RBAC 与审计增强、SSO（企业版方向）；
- 评测与回归集（基于 `request_traces` 构建 golden set）；
- 后台 IA 去客服化：`canned_scripts` → 通用 `response_templates`，`app_users` 合并、Ticket 领域拆分。

## [0.1.0] - 2026-10-20

首个对外版本。定位：**可私有化部署的对话式 Agent 运营平台** —— 多意图路由、知识库、工具调用、跨轮记忆与全链路追踪都做成平台能力，客服/售后只是首个场景包。

面向"能自己跑 Docker 的技术型团队"：克隆仓库 → 填 `.env` → `docker compose up -d` → 30 分钟内完成首次对话。

### 新增

**平台内核**

- 多意图路由：一条消息拆成多个意图按主次串行应答，追问自动续接上一轮 Agent；低置信主意图整轮转人工；兜底 Agent 可先应答再转人工（`ROUTER_MAX_INTENTS` / `ROUTER_CONFIDENCE_THRESHOLD` / `ROUTER_FALLBACK_ANSWER`）。
- 混合 Agent 注册：代码托管 Agent 与后台动态 Agent 同时注册，后台新增/停用即时进入路由候选，无需改代码重启（U03、U11）。
- 路由线索与去具名化：每个 Agent 声明 `routing_keywords` / `routing_examples`，路由提示词不偏向任何内置 Agent；链路详情回显意图命中依据（U12、U13、U15）。
- 全链路追踪：每轮对话的路由、LLM、工具、RAG、记忆调用写入 `request_traces.spans`，含耗时与错误；支持采样、保留期与清理脚本（U04）。
- 上下文工程：按系统提示 / 历史 / 记忆 / 知识分配 token 预算，超限按优先级裁剪（`CONTEXT_*`）。
- 跨轮记忆：working / episodic / perceptual 三层开箱可用，semantic 需自备 Neo4j；一轮只归档一次并放入后台任务，不拖慢首字（`MEMORY_*`）。
- 知识与 RAG：多命名空间知识库、文档上传/分块浏览/重建索引/原文下载/检索测试，向量存 Qdrant（U08）。
- 工具调用：代码自定义工具 + MCP（stdio / SSE / streamable HTTP），异步执行并记录结果与失败降级（U05、U09、U10）。
- 固定话术：高频确定性问题（身份、能力、探听类）在路由与大模型之前逐字命中，毫秒级响应、后台可改可停用（U16）。
- 双端身份：后台人员与客户分表、分登录入口、Token 隔离，客户可自助注册；401 单飞刷新后重放原请求（U07）。
- 工单：独立 `tickets` 表结构化增改查，支持跨轮补录与升级流转，可按场景包打 `metadata` 标记（U06、M4）。

**场景包机制（v0.1 的关键差异化能力）**

- `backend/app/packs/<pack>/manifest.yaml` 声明包内 Agent / 工具 / 知识库命名空间与 `features`；内核不 import 包内模块，由 `packs/loader.py` 反向注册，断包只记日志降级（M1）。
- 内置两个包：`customer_service`（3 Agent、9 工具、3 命名空间）与 `it_helpdesk`（3 Agent、4 工具、3 命名空间，含资产查询与 IT 工单）（U17）。
- 包级启停：`GET/PATCH /api/v1/admin/packs` + 运营台「场景包」页，复用 `agents.enabled`；停用客服包后 IT 包仍可对话与建单。运营台导航按 `features` 显隐。

**部署与运维**

- 一键部署：`docker-compose.yml`（postgres/pgvector、qdrant、redis、backend、web）一条命令拉起，五服务均带 healthcheck；后端仅绑回环，对外只暴露 8080。
- 生产叠加 `docker-compose.prod.yml`：Caddy 自动 HTTPS、`DATA_ROOT` bind mount、资源与日志限制、Redis/Qdrant 鉴权、一次性初始化（`INIT_ON_START=false`）。
- 初始化：首启自动执行 `init_db.sql`（幂等）；`SEED_DEMO=false`（默认）只创建一个管理员并**随机密码仅打印一次**；`SEED_DEMO=true` 才导入演示账号与示例数据。
- 自检与错误提示：启动日志单行输出数据库 / Qdrant / Redis / 模型状态，依赖不可用时给出可操作错误而不是长堆栈。
- 备份脚本 `scripts/backup.sh`：PostgreSQL dump + Qdrant 快照 + 业务数据 + Redis RDB，带保留期；Nginx/Caddy 样例含 SSE 关闭缓冲与 `TRUSTED_PROXIES` 说明。
- 离线/内网部署：`scripts/release.py bundle` 产出镜像 tar 包与校验和，配套 `docs/deployment.md` 离线章节。

**工程与前端**

- 前端单仓多包（npm workspaces）：`@cs/shared` 提供唯一的 HTTP 客户端（401 单飞刷新）、auth store、SSE 解析与基础类型；两个应用各自薄封装。
- CI：GitHub Actions 后端 ruff + pytest、前端两端 `npm ci && npm run build`。
- 社区物料：`LICENSE`（Apache-2.0）、`NOTICE`、`SECURITY.md`、`CONTRIBUTING.md`、issue/PR 模板、`.gitattributes`。
- 文档：`docs/quickstart.md`、`configuration.md`、`deployment.md`、`faq.md`、`runbook.md`、`packs.md`、`api.md`、`demo-script.md`、`positioning.md`、技术文章《多意图路由与提示词去具名化》。

### 变更

- 对外品牌统一为 **AgentDock**；仓库名、Compose 项目名与 Python 包名保留 `smart-cs`，避免已有数据卷失联。
- 客服领域资产从 `agents/`、`tools/custom/` 归位到 `packs/customer_service/`，内核与领域代码边界明确（M1）。
- 版本口径由内部 `1.3.0` 统一降为对外 `0.1.0`（`package.json`、`pyproject.toml`、FastAPI `version`、三个前端包与锁文件）。

### 修复（摘取影响使用的关键项）

- 业务 Agent 每轮新建导致跨轮失忆、工单无法补录 → 注入 history 并统一到工单表（U06）。
- 同步阻塞的 MCP 工具发现在事件循环上冻结整个 FastAPI 服务 → 超时隔离与线程执行（U05、U09）。
- 流式工具执行用同步 `_execute_tool_call` 导致 async 自定义工具不执行、不落库 → 自实现流式工具循环并回投主循环（U09）。
- 兜底意图被固定置信度与阈值拦截直接转人工 → 带 `is_fallback` 标记解耦判定（U14）。
- RAG 链路 namespace 未透传导致检索恒空、`chunk_count` 恒 0（U08）。
- 工单详情接口不回显 `metadata`（DB 有值、API 返回 None）→ `TicketRepo.to_dict` 补字段并加测试（M4）。
- `passlib 1.7.4` 与 `bcrypt 5.x` 不兼容导致 `hash_password` 报错；`hello-agents 0.2.9` 与 `qdrant-client>=1.10` 的 `SearchRequest` 不兼容（U01）。

### 已知限制

- **单实例单租户**：不支持水平扩容与租户隔离，`tenant_id` 仅预留字段。
- **无版本化迁移**：schema 变更靠幂等 `init_db.sql`，跨版本回滚需先用备份在预发环境演练。
- **无内置 mock 模型模式**：对话与向量必须连 OpenAI 兼容端点；内网需自备本地模型服务（详见离线部署章节）。`EMBED_MODEL_TYPE` 由框架支持 `dashscope`（配 `EMBED_BASE_URL` 时走 OpenAI 兼容 REST）/ `local` / `tfidf`，其中 `local` 依赖 sentence-transformers（当前镜像未包含）。
- semantic 记忆需 Neo4j，默认未启用。
- MCP 的 `npx` 型示例需外网；stdio 型服务要求容器内存在对应运行时。
- 运营台截图与演示 GIF 尚未随包发布（原截图清单所在文档已随场景包机制移除）。
- 前端构建有 chunk 体积告警；后台部分列表存在 N+1 查询（已列入发布后清单）。

### 升级方式

v0.1.0 是首个对外版本，无历史版本兼容义务。从内部构建升级：

```bash
bash scripts/backup.sh                 # 先备份（生产叠加需指定 COMPOSE_FILE）
git pull && git checkout v0.1.0
docker compose -f docker-compose.yml up -d --build
docker compose run --rm backend python ../scripts/init_db.py   # 幂等，补新增对象
```

破坏性变更会在本文件对应版本以 **⚠️** 标注；v0.1.x 期间接口与表结构仍可能调整，请在升级前阅读本节。

### 反馈与安全

- 部署卡点与功能建议：仓库 Issues（bug / feature / deployment help 模板）。
- 漏洞报告：见 [SECURITY.md](SECURITY.md)，请勿公开提 issue。
