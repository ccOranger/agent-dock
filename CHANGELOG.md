# 更新日志（CHANGELOG）

本文件记录 AgentDock（仓库代号 `smart-cs`）的所有重要变更。

- 格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
- **版本编号说明**：开发期仓库内部使用 `1.x` 迭代号；自首个对外版本起统一为 `0.1.0` 并以此递增。
  `package.json`、`backend/pyproject.toml`、`backend/app/main.py` 与 `frontend/admin-frontend/packages/shared` 的
  `version` 必须与本文件顶部「最新版本」一致，`python scripts/release.py check` 会强制校验。
- 每项能力的落地细节（背景、契约、文件、验证、边界）见 [docs/updates/](docs/updates/README.md)，本文件只做面向使用者的摘要。

## [Unreleased]

_暂无。_

## [0.1.0] - 2026-10-27

首个对外版本。定位：**可私有化部署的对话式 Agent 运营平台** —— 多意图路由、知识库、工具调用、跨轮记忆与全链路追踪都做成平台能力，客服/售后只是首个内置业务。

面向"能自己跑 Docker 的技术型团队"：克隆仓库 → 填 `.env` → `docker compose up -d` → 30 分钟内完成首次对话。

### 新增：版本化增量迁移（W1）

- **存量库可升级**：新增 `scripts/migrations/NNNN_slug.sql` + `schema_migrations` 记录表，存量库通过 `python -m app.migrate` 顺序升级，不再依赖 `rebuild_and_seed.py` 全量重建。空库仍由 `init_db.sql` 一次建到最新结构（不存 `0001_baseline.sql` 副本，避免双份 DDL 漂移）。
- **执行器**：`backend/app/core/migrate.py` 提供 `--check`（只读，有待执行退出码 1）、`--dry-run`（事务内试跑后回滚）、`--backup`（待执行时先 `pg_dump`）、无参正式执行；每个文件独立事务，成功才写历史记录，checksum 按 LF 归一化（跨 Windows/Linux 一致），历史文件被改直接报错中止。
- **启动守卫**：版本不匹配的错误提示从“全量重建”改为迁移命令（`python -m app.migrate`）；`init_db.sql` 末尾版本写入改 `ON CONFLICT DO NOTHING`，重复执行不会把存量库版本降回去。
- **生产保护**：`rebuild_and_seed.py` 在 `APP_ENV=production` 时默认拒绝执行（需显式 `--force`），文档定为仅开发/演示可用；`docker/backend-entrypoint.sh` 支持 `MIGRATE_ON_START=true`（默认 false）并对连接失败重试。
- **校验**：`scripts/verify_schema.py` 新增迁移记录一致性（编号不超前、checksum 未篡改、无待执行）与“当前库 vs `init_db.sql`”逐表逐列比对；新增 `backend/tests/test_migrations.py`（命名/排序/checksum/CRLF/历史篡改/dry-run/真实库数据保留）。
- **升级演练**：`test_g1_rehearsal_upgrade_keeps_data` 自造同规模存量库（500 消息 / 20 工单 / 3 知识库 + 30 文档 / 120 会话 / 200 埋点 / 60 记忆 / 50 链路），迁移前后 20 张业务表的行数与全行 md5 完全一致，dry-run 不落库，二次执行幂等。

### 新增：用量计量与报表（W2）

- **采集口径**：一轮对话结束后，把「路由提示词 + 业务 Agent 提示词 + 注入的历史 + 最终回答」估算成 token，写入 `chat_events.total_tokens` 并用 `tokens_estimated` 标记；`hello_agents` 未开 `stream_options.include_usage`，拿不到供应商 usage，故当前全部为估算值。
- **估算器**：新增 `backend/app/utils/tokens.py`。装了 `tiktoken`（**可选依赖** `backend/requirements-optional.txt`，镜像默认不装）按 `cl100k_base` 分词，否则按「CJK 1 字 ≈ 1 token、其余 4 字符 ≈ 1 token」加权；`TOKEN_ESTIMATE_FACTOR` 可按供应商账单整体校准。
- **与链路解耦**：用量计数不走 `TraceCollector.should_persist()`，`TRACE_ENABLED=false` 或 `TRACE_SAMPLE_RATE<1` 时 `request_traces` 没有记录，但账照记；异常/中断轮也会把已生成的部分计入。
- **日聚合**：新增 `chat_usage_daily(day, scene_code, integration_id)`（零 UUID 表示匿名/未归属，规避 NULL 在唯一约束中互不相等的坑），每次对话一条 `INSERT ... ON CONFLICT DO UPDATE` 累加 `requests / tokens / estimated_requests / handoffs / errors`；`chat_events` 仍按保留期清理，日聚合长期保留作账单依据。
- **接口**：`GET /api/v1/admin/usage/summary?from=&to=&group_by=day|scene|integration`（默认近 30 天，单次跨度上限 `USAGE_QUERY_MAX_DAYS`=366，超限 400）与 `GET /api/v1/admin/usage/export.csv`（UTF-8 BOM，(日 × 场景 × 接入方) 明细）；均要求 `admin`。
- **后台页面**：「用量报表」（`/usage`，admin）：日期区间、按天/场景/接入方切换、场景过滤、四张合计卡、Token 趋势柱状图（复用仪表盘样式，不引图表库）、明细表与 CSV 导出。
- **迁移**：`SCHEMA_VERSION` 由 `6` 升到 `7`，首个真实增量 `scripts/migrations/0007_usage_daily.sql`（`init_db.sql` 同步），存量库执行 `python -m app.migrate` 即可，无需重建。
- **测试**：`backend/tests/test_usage.py`（估算口径、估算系数校准、关链路仍计量、写入 SQL 与零 UUID 归属、接口参数/越权 403/CSV BOM）。
- **边界**：本项只产出「用量事实」，**不做**套餐、账单与支付；计费规则留给后续 License / 企业合同。

### 新增：License 与版本分层（W3）

- **离线授权文件**：`backend/app/core/license.py` 校验「JSON 负载 + Ed25519 签名」，不联网。公钥由 `LICENSE_PUBLIC_KEY` 提供（Base64 裸 32 字节或 PEM），私钥与签发脚本只在私有仓库，开源仓库不内置任何可用私钥。
- **只降级不阻断**：文件缺失 / JSON 解析失败 / 签名不符 / 指纹不符 / 已过期，一律退回 `community`；**不阻断启动、不影响对话与社区功能**（多场景路由、RAG、工具/MCP、跨轮记忆、链路追踪、用量报表、场景管理、嵌入挂件、基础评测全部保留）。
- **5 个功能位**：`rbac`、`audit`、`white_label`、`eval_pro`、`sso`，均不动对话内核；后端提供 `has_feature()` 与 `require_feature(feature)` 挂载点（社区版返回 403/2003），企业功能实现代码不进开源仓库。`eval_pro` 只控制评测集管理/定时回归/看板，不控制评测执行与报告。
- **部署指纹**：用 `.env` 的 `DEPLOYMENT_ID`（随数据卷长期存在）而不是机器码/hostname，避免容器重启即掉授权；留空表示未绑定（仅建议开发/演示）。
- **接口与页面**：`GET /api/v1/admin/license`（授权状态、功能位、到期天数、降级原因）与 `POST /api/v1/admin/license/reload`（换授权/续期免重启）；后台新增「授权与版本」页（`/license`，admin），降级或 30 天内到期时后台顶部常驻横幅。
- **启动自检**：启动日志输出一行 `授权: license=enterprise customer=… expires_in=365d features=…`；降级时 WARN 给出原因，便于现场一眼定位“文件放错位置 / 未配公钥 / 指纹不符”。
- **不升 schema**：本项无表结构变更，`SCHEMA_VERSION` 保持 `7`。
- **测试**：`backend/tests/test_license.py` 17 项（无文件/有效/过期/篡改功能位/缺签名/指纹不符/未配公钥/未知功能位/版本不支持/重新加载/功能位依赖 403 与放行/接口形状/agent 越权）。
- **边界**：不提供签发工具与私钥（在私有仓库）；不做机器码硬绑、代码混淆与在线激活；`mode`（`on_prem` / `hosted`）当前只记录，不做逻辑分支。

### 新增：评测与回归（W4）

- **用例集进仓库**：`backend/tests/fixtures/eval/<scene_code>.json` 首批 50 条（`customer_service` 39 + `default` 11），
  每条带 `expect` / `tags` / `source` / `note`；期望字段 18 个（意图、多意图、置信度、工具集与次数上限、RAG 命中/为空、
  转人工、兜底、关键词与禁词、应答长度、入口拦截）；`setup[]` 支持多轮铺垫只判最后一轮（追问续接与跨轮记忆类用例）。
  用例评审后才可改，**不允许改期望迁就结果**（口径修正必须在 `note` 里写清代码/政策依据）。
- **执行器走真实 HTTP 链路**：`scripts/eval.py` 打 `POST /api/v1/chat/stream`，不 import Agent，因此能覆盖鉴权 / 限流 /
  场景路由 / 固定话术 / 埋点 / 链路回读全链路；支持 `--scene` `--tag` `--limit` `--concurrency` `--baseline` `--dry-run` `--no-db`。
- **服务端事实回读**：SSE 流里没有的 RAG 片段数/命中命名空间/工具名/结局按 `done` 里的 `trace_id` 回读 `request_traces`，
  token 回读 `chat_events`（不受采样影响）—— 指标是实测而非模型自述。
- **分母纪律**：用例没写→不参与；写了但服务端无可回读事实（链路被采样丢弃）→计入 `unobserved`、不进分母；
  报告第 3 节把分子/分母/不可观测三个数都列出来，避免把基础设施问题误报成效果退化。
- **基线与退化闸门**：用例集指纹（只取 `case_id`+`query`+`setup`+`expect`，过滤后计算）锁定可比范围；比率类下降
  > 2 个百分点、耗时/token 上升 > 15% 判退化，退出码 `0/1/2/3`（2 = 相对基线退化，可当发布闸门）。
- **报告**：`reports/eval-<timestamp>.md` 五节结构（结论 → 与基线逐项对比含退化标注 → 指标口径与样本数 →
  失败用例含 `trace_id` 与摘录 → 全量用例表）+ 同名 sidecar `.json` 可当离线基线；`reports/` 不入库。
- **迁移**：新增表 `eval_runs`（运行结论 + 指标 + 逐用例明细 + 基线指向），`SCHEMA_VERSION` 由 `7` 升到 `8`
  （`scripts/migrations/0008_eval_runs.sql`）；与计划 3.4「并入 P0-2 升级」不同——W2 已交付验收，不为省一个版本号回头改已上线的 0007。
- **评测推动的两处产品修正**：修复 `/chat/stream` 响应头 `X-Trace-Id` 与落库 `trace_id` 不是同一个的缺陷（否则
  运维按响应头查链路永远查不到）；演示种子给 `knowledge_qa` / `order_service` 配 `rag_namespaces` 跨库绑定（政策类问法
  写在售后/订单指南里，只绑单库会检回空块只能转人工）。
- **首批实测**（本机 `SEED_DEMO=true`、`deepseek-flash`、`TRACE_SAMPLE_RATE=1.0`）：全量 36/39 = 92.3%，意图 30/30、
  工具 7/7、RAG 9/12；**同一用例集跑两次比率类指标逐项相同**（验收一）；把 `knowledge_qa` 提示词改错后意图准确率
  100% → 30%、4 项退化标注、退出码 2（验收二）；保留 3 条失败不迁就（演示文档整篇一块，窄问法落在 `RAG_MIN_SCORE` 之下）。
- **测试**：`backend/tests/test_eval_runner.py` 47 项全部 mock（不打真实 LLM）；打分/聚合/指纹/diff/报告渲染与仓库用例集结构校验。
- **边界**：后台「评测与回归」看板与定时回归属企业版功能位 `eval_pro`，**评测能力本身全在社区版**；
  首批来源为 `demo_corpus` 28 + `manual` 22，`trace_sample` 0 条（库里没有真实链路，偏离已记录）。

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

### 新增：场景身份话术、运行参数与登录人配额（U21）

- **场景身份/能力话术**：`canned_scripts` 的 `(scene_id, key)` 机制真正启用——新建场景自动复制全局 `identity` / `capability` 模板（`is_builtin=False`，可改文案/停用/删除），存量库跑 `scripts/backfill_scene_canned.py` 幂等补齐（不覆盖运营改过的行）；文案支持 `{{scene_name}}` / `{{scene_code}}` / `{{agent_names}}` 占位符，命中时渲染。后台「业务场景 → 固定话术」抽屉可查看/启停/一键从模板生成；路由档案对缺本场景身份话术的场景告警 `no_identity_script`（全局行存在不算已配置）。
- **知识库型话术**：`canned_scripts.answer_source`（`text` / `kb`）+ `kb_namespaces`。`kb` 模式检索后按固定序号拼接（`top_k=3`、单块 ≤180 字、总长 ≤800 字、硬超时 `min(场景超时, 3)` 秒、`advanced=False`、**不走模型改写**）；超时/零命中/异常一律回退，不向客户抛错。`reply_text` 在 kb 模式下只当引导语，允许为空。
- **场景运行参数**：`scenes.runtime_config` 白名单键 `rag.inject` / `rag.top_k` / `rag.min_score` / `rag.timeout` / `rag.namespaces` / `history_limit` / `quota.per_minute` / `quota.daily`；`{}` 或 `null` = 全部继承全局，未知键/越界/引用停用知识库返回 400。热路径零查库（`SceneContext` 已带解析好的 `SceneRuntime`）。`rag.namespaces` 显式配置时以场景为准（覆盖 Agent 声明）；默认场景只开放 `runtime_config`，路由模式/绑定/启停仍只读。
- **登录人创建配额**：`scenes` / `agents` / `knowledge_bases` 增加 `created_by`，`staff_users.quota`（`max_scenes` / `max_agents` / `max_knowledge_bases`）三态：键缺失/null=不限、`0`=禁止、`>0`=上限；超限返回 `3006` / 403 并写 `audit_logs("quota.reject")`；新增 `/api/v1/admin/staff`（列表/新建/PATCH/重置密码）与后台「人员与配额」页。
- **场景请求额度**：`acquire_chat_slot` 的 Redis 桶由 3 个扩到 5 个（主体分钟/日 + 场景分钟/日 + 主体并发流），仍一次 pipeline 提交；任一超限就回滚本次自增的全部键后返回 429（否则未参与判定的日桶与并发槽会白扣一个），新增额度不带来额外往返。**`quota.*` 不继承 `PUBLIC_CHAT_*`，默认 `0` = 不限**。
- **后台体验**：场景列表新增「今日请求 / 日额度」列（一条 `GROUP BY scene_code` 聚合，超 80% 标黄、达上限标红）；固定话术页新增「回答来源」与知识库多选，命中测试回显检索到的库，列表每行带 `scene_code`（场景已停用/归档也能正确定位，不会误改全局模板）；链路中 `canned.script` span 带 `answer_source` 与命中数。
- ⚠️ `SCHEMA_VERSION` 由 `5` 升到 `6`（`canned_scripts.answer_source/kb_namespaces`、`scenes.runtime_config/created_by`、`agents.created_by`、`knowledge_bases.created_by`、`staff_users.quota`、`idx_chat_events_created_scene`），旧库需 `scripts/rebuild_and_seed.py` 全量重建后跑一次 backfill。
- Agent 级模型配置已落地（`agents.llm_model` 按 Agent 指定 + `llm_factory` 按模型缓存）；尚未落地的是“模型供应商后台化”（API Key/Base URL 由后台维护而非 `.env`）。

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
- `/chat/stream` 响应头的 `X-Trace-Id` 与落库的 `trace_id` 不是同一个（中间件在 `call_next` 之后覆盖、端点又自己新生成一个）
  → 运维按响应头查链路永远查不到。端点改为复用 `request.state.trace_id`，由 W4 评测的回读环节发现。
- 后台链路页把日志里的 16 位链路 ID 粘进「会话 ID」框时 `uuid.UUID()` 抛 ValueError → 500
  （`/admin/traces` 列表、`/admin/traces/sessions/{id}`、`/admin/inquiries/sessions/{id}` 同类）
  → 新增 `app/api/params.py`：同一个 ID 框既认 36 位会话 UUID 也认 16 位 `trace_id`，
  非法形态返回 400 `code=1001`；列表接口另开放显式 `trace_id` 参数。

### 新增（基线能力）

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
- 工单：独立 `tickets` 表结构化增改查，支持跨轮补录与升级流转，可按场景打 `metadata` 标记（U06、M4）。

**领域资产登记（场景包机制已于本次发布前移除）**

- 早期用 `backend/app/packs/<pack>/manifest.yaml` 声明包内 Agent / 工具 / 知识库命名空间并按 `features` 显隐导航（M1、U17）；该机制已整体移除，Agent、提示词与工具迁回 `app/agents/` 与 `app/tools/custom/`，由 `registry.py` / `custom_catalog.py` 显式登记，能力与工具名保持不变。
- 当前划分：代码 Agent 与自定义工具是普通代码资产，业务差异通过「业务场景」在后台配置（Agent 池、知识库、话术、运行参数），不再需要打包与重启。

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
- 文档：`docs/quickstart.md`、`configuration.md`、`deployment.md`、`faq.md`、`runbook.md`、`evaluation.md`、`api.md`、`demo-script.md`、`positioning.md`、技术文章《多意图路由与提示词去具名化》。

### 变更（基线）

- 对外品牌统一为 **AgentDock**；仓库名、Compose 项目名与 Python 包名保留 `smart-cs`，避免已有数据卷失联。
- 客服领域资产曾在 M1 归位到 `packs/customer_service/`，本次发布前随场景包机制移除迁回 `agents/`、`tools/custom/`。
- 版本口径由内部 `1.3.0` 统一降为对外 `0.1.0`（`package.json`、`pyproject.toml`、FastAPI `version`、三个前端包与锁文件）。

### 修复（基线，摘取影响使用的关键项）

- 业务 Agent 每轮新建导致跨轮失忆、工单无法补录 → 注入 history 并统一到工单表（U06）。
- 同步阻塞的 MCP 工具发现在事件循环上冻结整个 FastAPI 服务 → 超时隔离与线程执行（U05、U09）。
- 流式工具执行用同步 `_execute_tool_call` 导致 async 自定义工具不执行、不落库 → 自实现流式工具循环并回投主循环（U09）。
- 兜底意图被固定置信度与阈值拦截直接转人工 → 带 `is_fallback` 标记解耦判定（U14）。
- RAG 链路 namespace 未透传导致检索恒空、`chunk_count` 恒 0（U08）。
- 工单详情接口不回显 `metadata`（DB 有值、API 返回 None）→ `TicketRepo.to_dict` 补字段并加测试（M4）。
- `passlib 1.7.4` 与 `bcrypt 5.x` 不兼容导致 `hash_password` 报错；`hello-agents 0.2.9` 与 `qdrant-client>=1.10` 的 `SearchRequest` 不兼容（U01）。

### 已知限制

- **单实例单租户**：不支持水平扩容与租户隔离，`tenant_id` 仅预留字段。
- **迁移需人工触发**：升级时执行 `python -m app.migrate`（或置 `MIGRATE_ON_START=true`），不提供自动滚动升级；回滚前请先用备份在预发环境演练。
- **无内置 mock 模型模式**：对话与向量必须连 OpenAI 兼容端点；内网需自备本地模型服务（详见离线部署章节）。`EMBED_MODEL_TYPE` 由框架支持 `dashscope`（配 `EMBED_BASE_URL` 时走 OpenAI 兼容 REST）/ `local` / `tfidf`，其中 `local` 依赖 sentence-transformers（当前镜像未包含）。
- semantic 记忆需 Neo4j，默认未启用。
- MCP 的 `npx` 型示例需外网；stdio 型服务要求容器内存在对应运行时。
- 演示数据的订单/物流时间为 2024 年（与示例订单号 `SO2024xxxx` 对应），模型可能提示“时间较旧”；接入真实工具后不存在此问题。截图与 GIF 见 [docs/assets/screenshots/](docs/assets/screenshots/) 与 [演示脚本](docs/demo-script.md)。
- 前端构建有 chunk 体积告警；后台部分列表存在 N+1 查询（已列入发布后清单）。

### 升级方式

v0.1.0 是首个对外版本，无历史版本兼容义务。从内部构建升级：

```bash
bash scripts/backup.sh                 # 先备份（生产叠加需指定 COMPOSE_FILE）
git pull && git checkout v0.1.0
docker compose -f docker-compose.yml up -d --build
docker compose run --rm backend python -m app.migrate   # schema 6 → 8，顺序升级
```

空库无需迁移：`init_db.sql` 一次建到最新结构（`SCHEMA_VERSION=8`）。

破坏性变更会在本文件对应版本以 **⚠️** 标注；v0.1.x 期间接口与表结构仍可能调整，请在升级前阅读本节。

### 后续版本方向

- RBAC 与审计查询/导出、SSO（企业版功能位方向）；
- 后台 IA 去客服化：`canned_scripts` → 通用 `response_templates`，`app_users` 合并、Ticket 领域拆分；
- 模型供应商后台化、`/metrics` 与诊断包、交付 SOP 与验收单。

### 反馈与安全

- 部署卡点与功能建议：仓库 Issues（bug / feature / deployment help 模板）。
- 漏洞报告：见 [SECURITY.md](SECURITY.md)，请勿公开提 issue。
