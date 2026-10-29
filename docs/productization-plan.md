# 产品化落地计划（v1.1）

> 状态：已评审，等待执行指令（2026-10-26 定稿，v1.1）。目标是把"能演示的产品"推进到"能签约、能交付、能续费的产品"。
> 关联文档：[产品定位](./positioning.md)、[开源发布检查清单](./open-source-release.md)、[部署](./deployment.md)、[运行手册](./runbook.md)。
> 基线：v0.1.0 已发布，多场景平台化 M0–M7 与 U18–U21 已落地，`SCHEMA_VERSION=6`（写作时基线；W2 升到 7、W4 升到 8）。
> 版本变更：v1 为初稿；v1.1 落定 4 项决策（第 8 节），补充启动条件（第 9 节）与排期硬依赖。
>
> **执行状态（2026-10-29 补记）**：P0-1 版本化迁移、P0-2 用量计量、P0-3 License 分层、P0-4 评测回归均已落地
> （见 [U22](./updates/22-usage-metering.md)–[U24](./updates/24-eval-regression.md)），P1-1 的 RBAC 也已落地（人员-角色-菜单矩阵，见 [U26](./updates/26-rbac-menu-matrix.md)）。
> 其中 P0-2 原计划的「后台用量报表页 + CSV 导出」已修正：请求数与每日趋势并入「数据分析」页，
> token 仅库内存留、不对外展示，CSV 导出取消（[U25](./updates/25-analytics-outcomes.md)）。下文为当时的决策原文，不再回改。

## 0. 判定标准：什么叫"能收钱"

不以功能数量衡量，只以四条可验证的事实衡量：

| 编号 | 判定项 | 验证方式 |
|---|---|---|
| G1 | 客户生产库能跨版本升级且不丢数据 | v6 库执行迁移到 v7，行数与抽样内容比对一致 |
| G2 | 能向客户出具用量账目 | 运营台「数据分析」按日×场景给出请求数/转人工/错误，可与链路抽样核对一致（原计划的 CSV 导出已随用量报表页下线） |
| G3 | 能区分社区版与企业版并驱动续费 | 无 license 可完整对话；有效 license 解锁企业项；到期前 30 天告警 |
| G4 | 能证明效果没有退化 | 同一 eval 集跑两版提示词，产出可对比报告 |

四条全部达成，才具备签第一单并交付的条件。其余工作都是放大系数。

## 1. 现状盘点（已核对代码）

| 能力 | 现状 | 对商业化的判断 |
|---|---|---|
| 产品定位 / ICP / 版本分层 / 定价原则 | [positioning.md](./positioning.md) 已定 | 无需重做 |
| 多场景隔离、公开接入、匿名归属、动态 CORS、嵌入挂件 | M0–M7、U18–U21 已落地 | 可交付 |
| 接入方与 API Key（`integrations` / `integration_keys`） | 已落地，U20 单租户下降级为 `ic_default` | **它就是计费主体**，逻辑必须保留 |
| 场景运行参数（`scenes.runtime_config`） | [runtime_config.py](../backend/app/services/runtime_config.py) 已支持 rag / history / quota 覆盖 | 客户调优入口已具备 |
| 资源创建配额（`staff_users.quota`） | [quota_service.py](../backend/app/services/quota_service.py) 已实现 | 是"限流"，**不是计费** |
| Agent 级模型（`agents.llm_model` → `get_llm()`） | **已落地**：[base_business_agent.py](../backend/app/agents/base_business_agent.py) L72 + [llm_factory.py](../backend/app/agents/llm_factory.py) 按模型缓存 | CHANGELOG 中"未落地"的描述需修正；真正缺的是**供应商后台化** |
| 角色权限 | `staff_users.role` 仅 `agent` / `admin`（[init_db.sql](../scripts/init_db.sql) L67），前端已按 `meta.roles` 守卫 | 粗粒度可用，不满足企业采购 |
| 审计 | `audit_logs` 有写入（如 `quota.reject`），无查询页、无导出 | 需补，否则无法响应客户审计诉求 |
| 数据库升级 | `init_db.sql` 幂等 DDL + `SCHEMA_VERSION` 启动守卫 + `rebuild_and_seed.py` **全量 DROP 重建**（[schema.py](../backend/app/core/schema.py)） | **最大阻塞项** |
| 用量采集 | `chat_events.total_tokens` 仅预留列，从未写入（[entities.py](../backend/app/models/entities.py) L457–458） | 需补采集与报表 |
| 评测回归 | 全库无相关实现 | 需从 0 建 |
| 可观测 | 无 `/metrics`、无告警、无诊断包 | 私有化支持成本会失控 |
| 依赖储备 | `alembic==1.13.2`、`prometheus-client==0.21.0`、`python-jose[cryptography]` **已在 requirements，未启用** | 三项 P0/P1 不必新增依赖 |

## 2. 差距总览

| 优先级 | 事项 | 阻塞什么 |
|---|---|---|
| P0-1 | 版本化增量迁移 | 客户升级即丢数据，第一单之后就会流失 |
| P0-2 | 用量计量与报表 | 讲不清账，无法定价、无法续费、SaaS 要重做 |
| P0-3 | License 与版本分层 | 无法区分社区/企业版，无法防转售，无到期驱动 |
| P0-4 | 评测与回归 | 无法证明效果，实施验收无凭证 |
| P1-1 | RBAC 与审计查询（企业版） | 客户 IT 部门审批门槛 |
| P1-2 | 模型供应商后台化 | 客户换模型要改 `.env` 重启，交付体验差 |
| P1-3 | 可观测与诊断包 | 私有化故障排查依赖客户截图 |
| P1-4 | 交付 SOP 与 SLA | 合同条款与报价方式 |
| P1-5 | 安全合规物料 | 采购流程硬门槛 |
| P2-1 | 白标 / OEM（企业版） | 溢价最高、投入最小 |
| P2-2 | 官网 / 定价页 / 试用 / 案例 | 获客，非代码 |
| P2-3 | IA 去客服化 | 长期品牌一致性 |
| P2-4 | 托管单租户（B） | 有条件的第二阶段，触发条件见 D5 |
| P2-5 | 多租户 SaaS（C） | 12 个月内明确排除（D5） |

---

## 3. P0 详细方案

### 3.1 版本化增量迁移（P0-1）

**问题**：`assert_schema_version()` 发现版本不符直接阻断启动，唯一出路是 `rebuild_and_seed.py`（DROP 全部业务表）。客户生产库里有会话、知识库、工单、链路，等价于"升级即清库"。

**目标**：任何版本 N 都能经 `NNNN_*.sql` 顺序升级到 N+1，数据零丢失，可 dry-run、可回滚提示。

**设计**

1. 新增 `scripts/migrations/` 目录，文件命名 `NNNN_slug.sql`（四位补零，只增不改）。
   - **不存 `0001_baseline.sql`**：baseline 直接复用 `scripts/init_db.sql` —— 空库一次建到最新结构，无需重放历史 DDL。
     这样只有一份 DDL，不会出现"基线副本与最新结构漂移"的维护负担。
   - 编号从 `0007` 开始（W1 落地时 `SCHEMA_VERSION=6`；随后 W2 已推出 `0007_usage_daily.sql`，当前 `SCHEMA_VERSION=7`，下一个增量从 `0008` 起）；已上线文件**永不修改**，只追加。
   - 代价：新库不验证历史迁移链，用两条约束兜住：增量必须幂等（可在最新结构上重放）、
     测试断言 `SCHEMA_VERSION == init_db.sql 末尾版本`。
2. 新增独立表 `schema_migrations(version PK, name, checksum, applied_at)`，由 `migrate.py` 在首次执行时 `CREATE TABLE IF NOT EXISTS`
   （因此**不需要**升 `SCHEMA_VERSION`，不破坏空库路径）。`schema_meta.version` 仍是版本唯一真相：迁移成功时一并 upsert。
3. 新增 `backend/app/core/migrate.py`：
   ```
   python -m app.migrate --check      # 只读：打印当前版本 / 待执行列表（有待执行退出码 1）
   python -m app.migrate --dry-run    # 每个文件事务内执行后回滚，验证 SQL 可行
   python -m app.migrate --backup     # 有待执行时先 pg_dump 到 BACKUP_DIR
   python -m app.migrate              # 顺序执行并记录
   ```
   - 每个文件在**独立事务**内执行，成功才写 `schema_migrations` + 更新 `schema_meta.version`。
   - 执行前比对 checksum（按 LF 归一化，避免 Windows CRLF 误判），与已记录不一致直接报错（防止改历史文件）。
   - 检测到空库（无 `schema_meta`）时先跑 `init_db.sql` 建到最新结构，不重放历史迁移。
   - asyncpg 整段 `execute(sql)` 走 simple query 协议，一次下发多语句，适配云库事务级代理。
4. `assert_schema_version()` 保持 `SCHEMA_VERSION` 断言（不改成读目录最大编号：编号超前于代码会导致启动守卫失效），
   但错误信息改为给出迁移命令（`python -m app.migrate`），不再暗示全量重建。
5. `rebuild_and_seed.py` 保留但加生产保护（决策 D4）：`APP_ENV=production` 时直接退出并提示改用 `python -m app.migrate`；确需重建必须显式 `--force`。文档标注为**仅开发/演示可用**；`scripts/README.md` 同步改写迁移规范。
6. 容器入口：`docker/backend-entrypoint.sh` 支持 `MIGRATE_ON_START=true`（默认 false，生产建议独立执行），并对连接失败做重试。

**落地步骤**

| 步骤 | 文件 | 说明 |
|---|---|---|
| 1 | `scripts/migrations/README.md` | 目录约定：命名、必须幂等、为何无 baseline、与 rebuild 的关系 |
| 2 | `backend/app/core/migrate.py` | 执行器 + CLI（check / dry-run / backup / apply） |
| 3 | `backend/app/core/schema.py` | 错误提示改为迁移命令 |
| 4 | `scripts/init_db.sql` | 末尾版本写入改 `ON CONFLICT DO NOTHING`，防止重复执行把版本降回 |
| 5 | `scripts/verify_schema.py` | 新增迁移记录一致性 + “当前库 vs init_db.sql”逐表逐列比对 |
| 6 | `backend/tests/test_migrations.py` | 命名/排序/checksum/CRLF/历史篡改/dry-run/真实库数据保留 |
| 7 | `docs/deployment.md`、`scripts/README.md` | 升级章节与迁移规范改写 |
| 8 | `scripts/rebuild_and_seed.py`、`docker/` | 生产保护 + `MIGRATE_ON_START` + COPY 迁移目录 |

**验收**：在含 500 条消息、3 个知识库、20 个工单的 v6 库上执行 `--dry-run` 与正式迁移，行数与抽样内容一致；二次执行无副作用；`--check` 输出准确。

> 已用 `backend/tests/test_migrations.py::test_g1_rehearsal_upgrade_keeps_data` 自造同规模演练库验证：
> 20 张业务表迁移前后**行数与全行 md5 完全一致**（500 消息 / 20 工单 / 3 知识库 + 30 文档 / 120 会话 /
> 100 访客 / 200 埋点 / 60 记忆 / 50 链路），dry-run 不落库，二次执行无待执行项。
> 客户真实库升级前仍建议在预发库跑一次 `--dry-run`。

**风险**：迁移 SQL 与 `init_db.sql` 结构漂移。**对策**：`verify_schema.py` 的 parity 检查已把 `init_db.sql` 建进一次性临时 schema 与当前库逐表逐列比对，纳入发版前校验。

---

### 3.2 用量计量与报表（P0-2）

**问题**：`chat_events` 全量记录但 `total_tokens` 恒为 NULL；`quota_service` 统计的是"创建了几个资源"，`ratelimit` 统计的是"分钟内请求数"，两者都不是账。Dashboard 只有工具/Agent 调用次数。

**设计（已落地）**

1. **采集**（`chat_events.total_tokens` 回填）
   - 优先取框架真实 usage：`hello_agents` 若在流式结束事件中返回 usage，则直接写入。
     **实测不支持**：`HelloAgentsLLM.think()` 用 `stream=True` 却未传 `stream_options={"include_usage": True}`，也不回传 usage，
     所以当前只能估算（保留 `tokens_estimated` 标记，将来接入真实 usage 时置 False）。
   - 兜底估算：`backend/app/utils/tokens.py`，优先 `tiktoken`（**可选依赖**：`backend/requirements-optional.txt`，Dockerfile 默认不装，决策 D3）；
     缺失时按中英混合字符加权（CJK 1 字 ≈ 1 token、其余 4 字符 ≈ 1 token），并置 `estimated=true`。
   - 校准：`TOKEN_ESTIMATE_FACTOR`（默认 1.0），客户对账时可按供应商账单微调。
   - 计量与链路**解耦**：计数不走 `TraceCollector.should_persist()`，`TRACE_ENABLED=false` / 采样丢弃时照样记账；
     异常轮把已生成的部分一并计入。口径 = 路由提示词 + 业务 Agent 提示词 + 注入的历史 + 最终回答。
2. **日聚合**（新增表，`SCHEMA_VERSION` → 7）
   ```sql
   CREATE TABLE chat_usage_daily (
       day             DATE NOT NULL,
       scene_code      VARCHAR(32) NOT NULL DEFAULT 'default',
       integration_id  UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
       requests        BIGINT NOT NULL DEFAULT 0,
       tokens          BIGINT NOT NULL DEFAULT 0,
       estimated_requests BIGINT NOT NULL DEFAULT 0,
       handoffs        BIGINT NOT NULL DEFAULT 0,
       errors          BIGINT NOT NULL DEFAULT 0,
       updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
       PRIMARY KEY (day, scene_code, integration_id)
   );
   ```
   - 用零 UUID 表示“匿名/未归属”，规避 PostgreSQL 唯一约束中 NULL 不相等的坑。
   - 写入方式：对话结束后一次 `INSERT ... ON CONFLICT DO UPDATE`（单条 SQL）累加全部计数；失败只告警，不影响对话。
   - 保留策略：`chat_events` 仍按保留期清理，`chat_usage_daily` 长期保留（账单依据）。
3. **接口**
   - `GET /api/v1/admin/usage/summary?from=&to=&group_by=day|scene|integration&scene_code=`
   - `GET /api/v1/admin/usage/export.csv?from=&to=`（UTF-8 BOM，(日 × 场景 × 接入方) 明细）
   - 均要求 `admin`；默认近 30 天；单次查询上限 366 天（`USAGE_QUERY_MAX_DAYS`），超限 400。
4. **前端**：`admin-frontend/src/views/UsageView.vue`，路由 `/usage`（`meta.roles=['admin']`），导航置于「仪表盘」之后。

**落地步骤**

| 步骤 | 文件 | 说明 |
|---|---|---|
| 1 | `scripts/migrations/0007_usage_daily.sql`、`scripts/init_db.sql` | 新增 `chat_events.tokens_estimated` + `chat_usage_daily` + 2 个索引，双方同步 |
| 2 | `backend/app/core/schema.py`、`backend/app/models/entities.py` | `SCHEMA_VERSION=7`；`ChatUsageDaily` 实体与零 UUID 常量 |
| 3 | `backend/app/utils/tokens.py`、`backend/requirements-optional.txt` | 估算器（tiktoken 惰性加载 + 字符加权回退 + 校准系数） |
| 4 | `backend/app/core/tracing.py`、`orchestrator.py`、`router_agent.py` | `record_prompt` / `record_completion`，路由、上下文工程、历史各处计数 |
| 5 | `backend/app/models/repositories.py` | `ChatEventRepo.record_usage`（明细回填 + 日聚合 upsert）、`UsageRepo.summary/export_rows` |
| 6 | `backend/app/services/chat_service.py` | 成功/异常两条路径都记账，且在 `done` 事件之前完成 |
| 7 | `backend/app/api/v1/admin_usage.py`、`router.py` | summary + export.csv，注册路由 |
| 8 | `admin-frontend/src/{views/UsageView.vue,api,types,router,layouts}` | 用量报表页与导航 |
| 9 | `backend/tests/test_usage.py`、`scripts/verify_schema.py` | 新表/新列纳入结构校验；估算、解耦、SQL、接口、CSV、RBAC 全覆盖 |
| 10 | `docs/{configuration,api}.md`、`CHANGELOG.md`、`.env.example` | 配置项、接口契约与变更记录 |

**验收**

- `backend/tests/test_usage.py` 14 项全通过：估算口径（CJK/ASCII/混排/空文本）、`TOKEN_ESTIMATE_FACTOR` 校准、
  `TRACE_ENABLED=false` 时 `prompt_tokens` 仍累加且不产 span、`record_usage` 的 UPDATE + `ON CONFLICT DO UPDATE` 语句形态
  与零 UUID 归属、summary 默认区间/维度透传/越界 400、CSV BOM 与表头、`agent` 角色 403。
- `chat_usage_daily` 的 `totals` 由同一查询的行结果累加，与 `rows` 必然自洽；`requests` 口径与 `request_traces` 一致（走完整链路的一轮）。
- 结构校验：`scripts/verify_schema.py` 已把 `chat_usage_daily`（9 列、2 索引）与 `chat_events.tokens_estimated` 纳入
  与 `init_db.sql` 的逐表逐列比对，避免迁移与基线漂移；本地真实库实测 28 项通过（含“迁移记录一致”“22 张表无漂移”）。
- 真实库端到端（验证数据用 `2000-01-01` 隔离并于跑后删除）：同一 `(日, 场景, 零 UUID)` 连写 3 次得
  `requests=3 / tokens=150 / estimated_requests=2 / handoffs=1 / errors=1`（确认是 upsert 累加而非覆盖）；
  `group_by=integration` 返回单行且 `unassigned=true`；`export_rows` 单行 `tokens=150`；明细 `chat_events` 回填到最新一次的值。
- 回归：全量 `pytest` 529 passed / 3 skipped，`ruff check app tests` 无告警，存量库 `python -m app.migrate` 6 → 7 后
  `--check` 无待执行项，`admin-frontend` `npm run build` 成功。

**边界**

- 本项只产出“用量事实”，**不做**套餐、账单、支付。计费规则在 P0-3 的 license 与企业合同中体现。
- **已知偏差**：token 为估算值，与供应商账单存在系统性偏差（随模型分词器而异），`TOKEN_ESTIMATE_FACTOR` 只能整体校准；
  真实 usage 需替换 `hello_agents` 的流式调用或改用自带 usage 的客户端。

---

### 3.3 License 与版本分层（P0-3）

**问题**：Apache-2.0 无功能分层，代码里没有任何 edition 概念，既不能防"云厂商托管转售"，也没有到期驱动续费的手段。

**设计（已落地）**

1. **授权文件**（离线，客户机房内校验，不联网）
   ```json
   {
     "v": 1,
     "customer": "某某科技有限公司",
     "edition": "enterprise",
     "features": ["rbac", "audit", "white_label", "eval_pro", "sso"],
     "issued_at": "2026-11-01T00:00:00Z",
     "expires_at": "2027-11-01T00:00:00Z",
     "deployment_id": "dep_7f3a...",
     "contract_ref": "HT-2026-0117",
     "signature": "base64(ed25519(...))"
   }
   ```
   - **不设场景 / 接入方数量硬上限**（决策 D2）：上限统一由 `staff_users.quota` 管理，避免两套机制冲突；`contract_ref` 仅用于对账与续期备注。
   - 签名用 `cryptography`（`python-jose[cryptography]` 已带入依赖，无需新增）。
   - **公钥不进仓**：由部署方写入 `.env` 的 `LICENSE_PUBLIC_KEY`（Base64 裸 32 字节或 PEM），
     私钥与签发脚本只在私有仓库。不在开源代码里固化任何可用私钥/占位公钥——占位公钥无对应私钥，
     实际无法验证，反而让人误以为“开箱可用”。
   - 校验代码本身留在开源仓库不影响防护：防护来自企业功能代码不在开源仓库（D6），而不来自校验逻辑的隐蔽性。
   - **指纹不用机器码**：容器 hostname 每次重启都会变，改用 `.env` 的 `DEPLOYMENT_ID`（初始化时生成一次，随数据卷长期存在），避免"重启即掉授权"。
2. **校验策略：只降级，不阻断**
   - 无文件 → `community`（完整对话能力，企业功能位关闭）。
   - 文件无效 / 签名不符 / 过期 → 降级 `community`，后台顶部红条提示，**不阻断服务**。付费客户生产停摆的代价远大于防破解收益。
   - 有效 → 按 `features` 解锁。
3. **功能位**（先只做 5 个，且都不动对话内核）
   | 功能位 | 控制点 |
   |---|---|
   | `rbac` | 精细化角色与权限矩阵（P1-1） |
   | `audit` | 审计日志查询与导出（P1-1） |
   | `white_label` | 品牌 / 主题 / 登录页 / 挂件品牌（P2-1） |
   | `eval_pro` | 评测集管理、定时回归、对比看板（P0-4 的增强部分） |
   | `sso` | OIDC / 企业微信 / 钉钉登录（后续） |
   - **社区版明确保留（不设功能位）**：完整对话与多意图路由、多知识库 RAG、工具 / MCP、跨轮记忆、全链路追踪、场景管理、嵌入挂件、数据分析（成效指标与每日请求量）、基础评测 CLI 与 Markdown 报告。
   - 只有上表 5 项属于企业版；`eval_pro` 只控制"评测集管理 / 定时回归 / 对比看板"，**不控制评测执行与报告**（决策 D1）——效果自证是获客手段，锁住等于削弱社区版说服力。
4. **落地**
   - `backend/app/core/license.py`：`load_license()`、`current_edition()`、`has_feature(name)`、`license_status()`（启动时算一次，存进程内）。
   - `backend/app/api/deps.py`：新增 `require_feature(feature)` 依赖。
   - `GET /api/v1/admin/license`：客户名、版本、功能位、到期天数、`edition`；前端 `/license` 页（置底"关于/授权"）。
   - 启动自检输出一行：`license=enterprise expires_in=365d`；到期 < 30 天时日志 WARN + 后台横幅。
   - `.env.example` 增加 `LICENSE_FILE`、`LICENSE_PUBLIC_KEY`、`DEPLOYMENT_ID`。
   - 测试：`backend/tests/test_license.py`（无文件 / 有效 / 过期 / 签名篡改 / 指纹不符 / 功能位开关）。
5. **签发工具**（私有仓库）：`license_issue.py --customer --edition --days --features --deployment-id`，输出文件与到期提醒清单。
   开源仓库只提供**校验**与**挂载点**，并提供 `POST /admin/license/reload` 让运维换文件后免重启。

**落地步骤**

| 步骤 | 文件 | 说明 |
|---|---|---|
| 1 | `backend/app/core/license.py` | `verify_document` 纯函数校验 + `load_license` / `reload_license`（进程内缓存）、`has_feature` / `license_status`、`log_startup_license`、`FEATURE_CATALOG` / `COMMUNITY_SCOPE` |
| 2 | `backend/app/core/config.py`、`backend/.env.example`、`.gitignore` | `LICENSE_FILE` / `LICENSE_PUBLIC_KEY` / `DEPLOYMENT_ID`；`license.json` 不入库 |
| 3 | `backend/app/api/deps.py` | `require_feature(feature)` 依赖工厂（社区版 403/2003，文案带功能中文名） |
| 4 | `backend/app/api/v1/admin_license.py`、`router.py` | `GET /admin/license`、`POST /admin/license/reload`（require_admin） |
| 5 | `backend/app/main.py` | 启动时输出一行 `license=<edition> expires_in=<days>d`，降级/临期 WARN |
| 6 | `admin-frontend/src/{stores/license.ts,views/LicenseView.vue,router,layouts,api,types}` | 授权页 `/license`（admin）+ 降级/临期顶部横幅 |
| 7 | `backend/tests/test_license.py` | 无文件/有效/过期/篡改/缺签名/指纹不符/未配公钥/未知功能位/版本不支持/重载/依赖 403/接口形状 |
| 8 | `docs/{configuration,api}.md`、`docs/updates/23-license-edition.md`、`CHANGELOG.md` | 授权文件格式、签名口径、接口与变更记录 |

**验收**

- 同一次构建，放入不同 license 文件即可切换 `community` / `enterprise`；删文件后仍为 community 但对话正常。
- 篡改 `features` 后签名校验失败并降级；授权里的 `deployment_id` 与本机 `DEPLOYMENT_ID` 不符同样降级。
- 过期后 `active_features` 清空、`expired=true`，后台展示“曾授权”但企业接口全部 403；续期后点“重新加载”即恢复。
- 真实签发端到端已验证：有效=`enterprise/364 天/3 个功能位`，篡改=签名失败，过期=community+`days_left=-3`，
  换指纹/删文件/只放文件不配公钥均降级且给出具体原因；`test_license.py` 17 项全通过，`ruff` 与前端构建通过。
- **无 schema 变更**：`SCHEMA_VERSION` 保持 `7`，升级不涉及数据库。

**边界**

- 签发工具、私钥与续期清单在私有仓库；开源仓库不内置任何可签发的密钥。
- 不做机器码硬绑、代码混淆、在线激活（D6）：防护来自企业功能代码不在开源仓库，而非校验逻辑隐蔽。
- `mode`（`on_prem` / `hosted`）当前只记录不分支；转托管模式时重新评估。
- 功能位对应的企业页面（RBAC 矩阵、审计查询、白标、评测看板、SSO）在 W5 起于私有仓库实现，
  本期只交付挂载点与后台授权页。

---

### 3.4 评测与回归（P0-4）

**问题**：客户第一问是"效果行不行"；改提示词、换模型、调 RAG 参数后没有任何手段证明没退化。没有它，"效果承诺"和"实施验收单"都无从谈起。

**设计（已落地）**

1. **用例集**：`backend/tests/fixtures/eval/<scene_code>.json`
   ```json
   { "case_id": "cs-001", "scene": "customer_service", "query": "我要退昨天买的耳机",
     "expect": { "intent": "complaint", "tool": "create_ticket", "kb": "cs_after_sales" },
     "tags": ["售后", "退款"] }
   ```
   - 首批 30–50 条，来源分层：60% 真实 `request_traces` 抽样、40% 人工构造边界用例（低置信、超纲、多意图、追问）。
   - 用例进 Git，评审后才可改，避免"改用例迁就结果"。
2. **执行器**：`scripts/eval.py`
   - 走**真实 HTTP 链路**（`POST /chat/stream`）而非直接调 Agent，覆盖鉴权 / 限流 / 追踪 / 话术全链路。
   - 支持 `--baseline` 对比上一次结果，`--scene`、`--tag`、`--concurrency`。
   - 结果写 `eval_runs` 表（run_id、版本、模型、开始/结束、指标 JSON），明细写文件。
3. **指标**：意图准确率、工具命中率、RAG 命中率、兜底率、转人工率、P95 首字延迟、平均 token。
4. **报告**：`reports/eval-<timestamp>.md`，含与 baseline 的逐指标 diff（退化标红），作为交付验收附件。
5. **后台（企业版 `eval_pro`）**：只读页展示最近 5 次运行与趋势；社区版只提供 CLI 与 Markdown 报告。

**落地清单**

| # | 文件 | 内容 |
|---|---|---|
| 1 | `backend/app/schemas/eval.py` | 用例/期望/观测/结果/指标数据模型，18 个可选期望字段与加载校验 |
| 2 | `backend/app/services/eval_runner.py` | 纯逻辑：指纹、打分、聚合、基线 diff、报告渲染（不发请求、不碰 DB） |
| 3 | `scripts/eval.py` | 执行器：真实 HTTP 采集 + 按 `trace_id` 回读链路 + 并发 + 基线 + 报告 + 写库；退出码 0/1/2/3 |
| 4 | `eval_runs` 表 + `scripts/migrations/0008_eval_runs.sql` + `init_db.sql` + `verify_schema.py` | `SCHEMA_VERSION` 升到 `8` |
| 5 | `backend/tests/fixtures/eval/{customer_service,default}.json` | 首批 50 条（39 + 11），含 `source` 与 `note` |
| 6 | `backend/tests/test_eval_runner.py` | 47 项全部 mock，不打真实 LLM |
| 7 | `docs/evaluation.md`（使用手册）、`docs/updates/24-eval-regression.md`、`CHANGELOG.md`、`configuration.md` 3.16、`runbook.md` 6.1 | 文档与运维入口 |

**与计划的两处偏离（已记录于 [U24 §6](updates/24-eval-regression.md)）**

1. `eval_runs` **没并入 P0-2 的升级，而是独立升到 `8`**：W2 已交付并验收完，为省一个版本号回头改已上线的 0007 风险大于收益。
2. 来源分层的 60/40 未达成：仓库交付时库里没有真实客户链路，首批实际 `demo_corpus` 28（56%）+ `manual` 22（44%）+ `trace_sample` 0 条；
   用 `source` 字段把替换进度显式标出，单测锁住“`demo_corpus` 可换成 `trace_sample` 而不改结构”这个约定。

**验收**（均已拿到自造证据，本机 `SEED_DEMO=true` / `deepseek-flash` / `TRACE_SAMPLE_RATE=1.0`）

- 同一用例集跑两次，指标一致：`w4-full-3` vs `w4-full-4 --baseline latest`（39 条）—— 通过率 92.3%、意图 30/30、工具 7/7、
  RAG 9/12、转人工 4/38 **逐项相同**，只有 P95 首字 +988ms、平均 token +51 在容差内 → 结论「无指标退化」。
- 把某业务 Agent 的提示词改错后，报告能明确显示意图准确率下降：`w4-deg-base`（`--tag 知识` 10 条，基线）→
  把 `knowledge_qa` 提示词与路由线索改成「物流异常登记」→ `w4-deg-broken` 意图准确率 **100% → 30%**，
  报告标 4 项退化（通过率 −40%、意图 −70%、RAG −40%、转人工 +70%），退出码 **2**；跑完配置已逐项恢复并读回校验。
- 报告可直接附在验收单后：五节结构（结论 → 指标与基线对比含退化标注 → 指标口径与样本数 → 失败用例 → 全量用例表），
  每条失败带 `trace_id` 可回跳后台链路页。
- 真实链路跑出的附加价值：发现并修掉了 `X-Trace-Id` 与落库 `trace_id` 不一致的产品缺陷（直接 import Agent 测不出来）。

**边界**

- 后台评测看板与定时回归属企业版功能位 `eval_pro`，**评测能力本身全部留在社区版**（D1）。
- 模型输出带随机性 → 措辞类只允许 `keywords_any` 这类宽松判定；P95 用最近邻法不插值。
- token 为估算值（同 P0-2 口径）；首字延迟由客户端计时，与后台 `ttft_ms` 不同源。
- 用例集是场景相关的：换客户需重写 `<scene>.json`，判定逻辑与执行器不动。

---

## 4. P1 详细方案

> 代码归属按 D6 划分：本节的精细权限模型、审计查询与导出页、白标配置、`eval_pro` 后台页、SSO 适配器**落在私有仓库**；开源仓库只提供挂载点（`require_feature()`、角色常量、license 校验骨架与公钥）。模型供应商后台化、`/metrics`、诊断包属于内核能力，留在开源仓库。

### 4.1 RBAC 与审计查询（`rbac` / `audit` 功能位）

> 实现现状（2026-10-29）：开源版已落地**模块级**角色矩阵——三张表 `roles` / `user_roles` / `role_permissions`、
> `app/core/rbac.py` 的 `MENU_MODULES` 与 `require_perm()`、`/admin/rbac/*` 与「角色与权限」页，
> 且支持自定义角色（见 [U26](./updates/26-rbac-menu-matrix.md)）。下方「代码常量 + 固定角色」与
> `owner`/`viewer`、资源级 `require_permission("scene:write")` 仍属企业版设计，尚未实现。

- 角色扩展为 `owner` / `admin` / `agent` / `viewer`；`owner` 唯一且不可删除（防锁死）。
- 权限矩阵用代码常量（`app/core/permissions.py`）而不是表：本期角色固定，建表会增加迁移与校验成本；将来真需要自定义角色再升表。
- `require_permission("scene:write")` 依赖替换现有 `require_admin`；保留 `require_admin` 作为兼容别名。
- 新增 `GET /api/v1/admin/audit-logs`（分页、按 actor/action/时间过滤）、`GET /api/v1/admin/audit-logs/export.csv`。
- 前端新增 `AuditLogsView.vue`（`meta.roles=['admin']`，`audit` 功能位）。
- 测试：越权矩阵、owner 不可删、导出编码。

### 4.2 模型供应商后台化

- 新增 `model_providers` 表：`name`、`base_url`、`api_key_enc`（用 `app/core/security.py` 现有对称加密或环境密钥派生）、`models JSONB`、`enabled`。
- `agents.llm_model` 支持 `provider/model` 语法；`llm_factory.get_llm()` 解析前缀取对应 base_url/key，缓存键改为 `provider/model`。
- 后台新增"模型供应商"页（`meta.roles=['admin']`），提供"连通性测试"（同 `setup.py` 的最小请求）。
- 保留纯环境变量模式作为默认，未配置供应商时行为完全不变（零回归）。
- 注意：`llm_factory` 现有缓存是进程级的，改 key 后需提供"清缓存"接口，避免换 Key 后仍用旧客户端。
- 测试：解析、缓存隔离、测试连接失败提示、未配置时回退。

### 4.3 可观测与诊断包

- `GET /metrics`（`prometheus-client` 已在依赖）：对话请求数/延迟直方图、LLM 调用数、工具失败数、RAG 空命中数、license 到期天数。
- `/metrics` 默认仅内网可访问（校验来源 IP 或 `METRICS_TOKEN`），不进 CORS 放行。
- `GET /api/v1/admin/diagnostics/bundle`：打包版本、schema 版本、许可证状态、自检结果、脱敏配置、最近 200 条 ERROR 日志，产出 zip 供客户回传。**必须脱敏**（API Key、数据库密码、手机号）。
- 测试：脱敏断言（不得出现 `sk-`、密码、手机号正则）。

### 4.4 交付 SOP 与 SLA

- `docs/runbook.md` 增补：升级（备份 → `--check` → `--dry-run` → 迁移 → 冒烟）、回滚（恢复备份 + 回退镜像）、备份恢复演练步骤与记录表。
- 形成《交付验收单》：部署检查项、eval 报告、用量基线、已知限制四部分。
- SLA 草案：响应时限、严重级别定义、支持渠道与"尽力而为"边界（与 SECURITY.md 口径一致）。

### 4.5 安全合规物料

- CI 增加 `pip-audit` 与 `npm audit`，已知问题记录在 `SECURITY.md`。
- 复核 `TRACE_STORE_PAYLOAD` 默认值，生产默认不落客户正文（已改为默认 `false`）。
- 依赖许可证扫描（open-source-release.md §2 / §7 中仍待办的合规项）。
- 文档化：日志留存期、数据删除流程（客户离场时如何清库）、脱敏范围。

---

## 5. P2 概览（不进本期排期）

| 项 | 说明 |
|---|---|
| 白标 / OEM | 复用挂件已有 theme 下发机制，扩展 Logo、登录页、邮件/通知品牌；受 `white_label` 功能位控制 |
| 官网 / 定价页 / 试用 / 案例 | 截图与 GIF（发布清单仍未完成）→ 定价页 → 静态试用环境 → 2–3 个部署案例 |
| IA 去客服化 | `canned_scripts → response_templates`、Ticket 领域拆分、`app_users` 合并（CHANGELOG 已列为候选） |
| 多租户 SaaS（C） | 依赖 P0-2 的计量口径与 `integration_id` 归属；D5 已排除 12 个月内实施，重启评估条件见第 8 节 |
| 托管单租户（B） | 一客户一套 compose + 一库，自动开通与订阅计费；复用现有架构，不需全库唯一性改造；仅当第 8 节四条触发条件满足两条时启动 |
| 前端性能 | chunk 体积告警、后台列表 N+1（`test_*` 已覆盖部分，可延后） |

---

## 6. 排期（8 周，单主线）

| 周 | 交付 | 出口条件 |
|---|---|---|
| W1 | 迁移体系 + CI 结构比对 | G1 达成；v6→v7 演练零丢失 |
| W2 | 用量采集 + `chat_usage_daily` + 用量页/CSV | G2 达成 |
| W3 | License + 功能位 + 授权页 | G3 达成 |
| W4 | 评测执行器 + 首批 40 条用例 + 报告 | G4 达成 |
| W5 | RBAC + 审计查询/导出 | 越权测试全绿 |
| W6 | 模型供应商 + `/metrics` + 诊断包 | 换模型不停机；脱敏测试通过 |
| W7 | 交付 SOP + 验收单 + 安全合规收口 | 可交付文档齐备 |
| W8 | 试点客户验收 + 发布 0.2.0 + 物料 | 首个客户升级成功 |

> 每个 P0 必须有对应 `backend/tests/test_*.py`。原计划「schema 变更统一在 W2 一次性升到 7（含 `chat_usage_daily`、`eval_runs`）」
> 实际没合并：`chat_usage_daily` 在 W2 升到 `7`，`eval_runs` 在 W4 独立升到 `8`（`0008_eval_runs.sql`）——
> 回头修改已交付并上线的 0007 迁移，风险大于省一个版本号带来的打扰。
> **进度**：W1（G1）、W2（G2）、W3（G3）、W4（G4）已交付，详见 `CHANGELOG.md` 的 `[Unreleased]`（W1–W4 各一节）
> 与 [U22](updates/22-usage-metering.md)、[U23](updates/23-license-edition.md)、[U24](updates/24-eval-regression.md)；W1 的迁移体系没有独立 U 文档，口径在 [deployment.md](deployment.md) 与本文件 3.1。
> 每项完成后同步更新 `CHANGELOG.md` 与 `docs/updates/NN-*.md`（项目既有习惯）。
> **硬依赖，不得并行穿插**：W2 依赖 W1 的迁移执行器（否则客户库只能重建）；W5 的 RBAC 依赖 W3 的 `require_feature()`（否则功能位没有挂载点）；P2 白标依赖 `white_label` 功能位。
> **代码归属**：W5/W6 中受企业功能位控制的部分在私有仓库开发，开源仓库只提交挂载点与测试桩；W1–W4 与 W6 的内核部分（迁移、计量、license 校验、评测 CLI、`/metrics`、诊断包）全部在开源仓库完成。

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 迁移在客户旧库执行失败 | 生产中断 | 强制 `--backup`；`--dry-run` 先行；单文件单事务；失败即停并保留已成功记录 |
| `init_db.sql` 与迁移链结构漂移 | 新库与老库行为不一致 | CI 双路径建库比对 `information_schema` |
| token 估算与实际账单偏差 | 客户对账争议 | 优先真实 usage；估算行标 `estimated`；提供校准系数并在报表注明口径 |
| license 误伤导致生产不可用 | 严重客户事故 | 只降级不阻断；到期后仍可对话；降级仅关企业功能位 |
| 评测集过拟合提示词 | 报告好看但线上退化 | 用例 60% 来自真实 trace；用例评审后冻结；保留线上抽检 |
| 8 周计划被打断 | 节奏失控 | P0 四项可独立发布，W4 后即可开始接触首个客户 |

## 8. 决策记录（2026-10-26 已定）

| 编号 | 议题 | 结论 | 理由 | 落地约束 |
|---|---|---|---|---|
| D1 | 企业版功能边界 | 基础评测（用例集 + CLI + Markdown 报告）留在社区版；`eval_pro` 只卖评测集管理、定时回归与对比看板 | 效果自证是获客工具，锁住会削弱社区版说服力；客户真正肯付费的是"可持续的回归管理" | `scripts/eval.py` 与 `tests/fixtures/eval/` 不设功能位；后台评测页受 `eval_pro` 控制 |
| D2 | 授权粒度 | 按实例年授权：一个 `DEPLOYMENT_ID` 对应一份 license，覆盖该部署内全部场景与接入方 | 与 positioning 的"实例 + 年度"定价原则一致；按接入方计数会与单租户降级（U20）的默认接入方语义冲突 | license 不设 `max_scenes` 硬上限，避免与 `staff_users.quota` 形成两套上限；只保留 `contract_ref` 备注合同号 |
| D3 | token 计量依赖 | `tiktoken` 列为可选依赖，默认不装 | 内网交付对镜像体积敏感；估算兜底已能满足对账精度 | 新增 `backend/requirements-optional.txt`；Dockerfile 不安装；缺失时自动降级并置 `estimated=true` |
| D4 | `rebuild_and_seed.py` | 保留，但生产环境拒绝执行 | 开发/演示仍需要"一条命令回到干净状态"；必须防止运维误在生产库执行 | `APP_ENV=production` 时直接退出并提示 `python -m app.migrate`；确需重建须显式 `--force` 并二次确认 |
| D5 | 交付模式选择 | **A 私有化 + 实例年授权为主**；B 托管单租户（一客户一套容器与库，自动开通计费）仅在满足触发条件时启动；C 共享多租户 SaaS 12 个月内排除 | A 与现有单实例单租户架构、ICP、现金流完全匹配；B 复用同架构只加编排与计费；C 需全库唯一性改造且客户画像与 ICP 错配 | license 预留 `mode: on_prem \| hosted` 字段（当前仅记录，不做逻辑分支）；C 重启评估条件：单客户机器成本 > 该客户收入 20% 且客户数 > 50 |
| D6 | 企业版代码边界 | 内核与社区版继续 Apache-2.0 单仓；**企业版功能进私有仓库**，以镜像 / 私有包交付；license 只降级不阻断；不做混淆、加密狗与机器码硬绑 | 企业功能若留在 Apache 仓库，删一行 `has_feature()` 即失效，license 形同纸糊；ICP 要的是"数据不出内网"而非"拿到源码"；混淆与硬绑会推高运维成本却不增安全 | 开源仓库只保留挂载点（`require_feature()`、角色常量、license 校验骨架与公钥）；RBAC 精细权限、审计查询页、白标、`eval_pro` 后台页、SSO 落私有仓库；客户坚持要源码时签 escrow 协议而非直接交付 |

> B 的启用触发条件（任意满足两条）：① 连续 3 个月每月 ≥ 2 个客户因"不想自己运维"流失；② 客单价稳定 ≥ 2 万/年；③ 已有 ≥ 3 个私有化客户在真实流量下运行；④ 有第二人可接手值守。
> 本节决策已定稿，后续如需变更按"决策修订"追加一行，不覆盖原记录。

## 9. 启动条件（Definition of Ready）

代码开工前需要就位的环境与物料，缺任一项都可能让 W1 卡住：

| 项 | 内容 | 责任 |
|---|---|---|
| 演练库 | 一份接近真实的 v6 库样本（≥50 会话 / ≥3 知识库 / ≥20 工单），脱敏后存本地 | 交付侧 |
| 分支与版本 | 从 `main` 切 `feat/productization`，目标版本 `0.2.0` | 开发 |
| 回滚通道 | 演练库 `pg_dump` 备份 + 当前镜像 tag 记录 | 交付侧 |
| 模型凭据 | 一个低成本模型端点专供 eval，避免回归测试烧钱 | 开发 |
| 用例来源 | 从 `request_traces` 导出近 30 天真实问题清单（脱敏） | 开发 |
| 签发环境 | Ed25519 私钥与签发脚本只在私有仓库，公钥提交到开源仓库 | 负责人 |

## 10. 本期明确不做

- 自助注册、在线支付、账单系统（定位文档已排除）。
- 多租户隔离与水平扩容。
- SSO 具体对接（仅保留功能位）。
- 通用 DAG 编排、插件市场、移动端。
- 为单个客户 fork 代码。
