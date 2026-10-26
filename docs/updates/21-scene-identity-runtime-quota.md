# U21 场景身份话术、运行参数与登录人配额

## 背景

四个诉求，本期落地三个（第四个「Agent 级模型配置」整体延后，见文末）：

1. 不同业务场景下客户问「你是谁」，应该得到对应场景的回答；固定话术要能按场景配，或者改为查知识库；
2. 业务场景要能配置运行参数（检索条数/超时/历史条数/并发意图等），而不是只能改 `.env` 全局重启；
3. （延后）Agent 支持独立模型配置；
4. 对登录人的创建数量（场景/Agent/知识库）与场景请求次数做可动态调整的限制。

改造前的三个具体缺口：

- `canned_scripts` 只有一张表，`identity`/`capability` 全平台共用一句文案，
  换个场景只能说同一套「我是谁」；同 key 的场景行机制存在，但没有播种、没有后台入口；
- RAG / 历史条数 / 限流全部读全局 `Settings`，改一个场景要动环境变量并重启；
- 后台任何 staff 都能无限建场景、建 Agent，也没有场景维度的请求总量上限。

设计细节与逐项取舍见 [../scene-identity-runtime-quota.md](../scene-identity-runtime-quota.md)，
本文只记录最终落地的实现与契约。

## 设计

### 1. 场景身份/能力话术（P1）

| 项 | 处理 |
|---|---|
| 数据 | 复用 `canned_scripts` 的 `(scene_id, key)` 唯一约束，`scene_id IS NULL` = 全局模板 |
| 播种 | `seed_data.py` / `rebuild_and_seed.py` 给每个场景复制一份 `identity`、`capability`（`is_builtin=False`，可改可删） |
| 新建场景 | `POST /admin/scenes` 建完场景后调 `ensure_scene_templates_committed`，只补缺失 key，失败只告警不回滚场景 |
| 存量库 | `scripts/backfill_scene_canned.py`：幂等，缺哪条补哪条，**不覆盖**运营改过或停用的行 |
| 后台警告 | 路由档案只把「本场景自有且启用」的 key 算作已配置，缺 `identity` 追加 `no_identity_script`（全局行存在不算） |
| 文案 | 种子文案带占位符 `{{scene_name}}` / `{{scene_code}}` / `{{agent_names}}`，命中时渲染 |
| 列表回包 | 每行带 `scene_code`（一次 `SceneRepo.list_all()` 建映射）：已停用/归档场景不在 `/scenes/options` 里，前端靠它定位行，避免误把场景行当全局行编辑 |

为什么不运行时继承：继承会把「改一个场景」变成「改所有场景」，运营永远不敢动文案；
复制之后每个场景各自独立，改错也只影响自己。

### 2. 知识库型话术（P1）

`canned_scripts` 增加两列：

- `answer_source`：`text`（默认，逐字下发 `reply_text`）/ `kb`（检索后拼接）；
- `kb_namespaces`：`kb` 模式的检索范围，JSONB 数组。

kb 路径**不走 LLM 改写**：检索 → 清洗 → 按固定序号拼装（`KB_TOP_K=3`、单块 ≤180 字、总长 ≤800 字、
硬超时 `min(场景超时, 3)` 秒、`advanced=False`），失败/超时/零命中一律回退，不向客户抛错；
启用库清单按 10s 进程内缓存（命中话术在首字节路径上，不该每轮全表查一次知识库；
停用库最多晚 10s 生效）。
链路里 `canned.script` span 带 `answer_source` 与命中数，便于排障。

`reply_text` 在 kb 模式下只当引导语，允许为空；切回 `text` 时保留 `kb_namespaces` 不报错，便于来回切换。

### 3. 场景运行参数（P2）

`scenes.runtime_config JSONB`，只存显式覆盖项：

| 键 | 范围 | 缺省 |
|---|---|---|
| `rag.inject` | bool | `true` |
| `rag.top_k` | 1–20 | `RAG_TOP_K` |
| `rag.min_score` | 0–1 | `RAG_MIN_SCORE` |
| `rag.timeout` | 1–60 | `RAG_TIMEOUT` |
| `rag.namespaces` | ≤10，必须是启用中的库 | `[]` |
| `history_limit` | 1–50 | 50 |
| `quota.per_minute` | 0–600 | **0（不限）** |
| `quota.daily` | 0–100000 | **0（不限）** |

- 解析在 `app/services/runtime_config.py`：`normalize`（白名单/范围/去重，未知键 400）→
  `validate_runtime_namespaces`（异步查库）→ `resolve_runtime`（合并全局，产出 `SceneRuntime` 与每键来源）。
- `rag.namespaces` **显式配置时以场景为准**（覆盖 Agent 自身的 `rag_namespace(s)` 与
  `search_all_knowledge_bases`）；未配置才回到 Agent 声明。否则会出现「来源标着本场景覆盖、
  实际检索范围没变」的假生效，且收敛检索范围是安全方向，不能让配置白填。
- 热路径零查库：`SceneContext` 在 `build_scene_context` 时已带 `runtime`，`orchestrator` / `ContextEngineer`
  / `ChatService` 直接用快照。
- 请求体内 `{}` 或 `null` = 恢复全部继承；默认场景只开放 `runtime_config`，路由模式/绑定/启停仍只读。
- `quota.*` 是**唯一不继承**全局的键：`PUBLIC_CHAT_*` 限的是单主体，场景额度限的是整场景，
  把「30 次/分」当场景总额会让多用户场景莫名被限。

### 4. 登录人配额与场景额度（P4）

**创建配额**：`staff_users.quota JSONB`，键 `max_scenes` / `max_agents` / `max_knowledge_bases`。

| 值 | 含义 |
|---|---|
| 键缺失 / `null` | 不限 |
| `0` | 禁止创建 |
| `> 0` | 上限 |

- 只统计 `created_by = 当前登录人`；`created_by IS NULL`（默认场景、seed 代码 Agent、演示数据）不计入任何人；
- 删除立即释放，编辑不消耗，归档场景仍占用（行还在）；
- 超限 `QuotaExceeded`（`3006` / 403），并写 `audit_logs(action="quota.reject")`；
- 并发用进程内 `(user_id, kind)` 的 `asyncio.Lock` 串行化校验与创建，多进程部署接受极小超额窗口（后台低频操作）。

**场景额度**：`acquire_chat_slot` 的桶从 3 个变 5 个（主体分钟/日 + 场景分钟/日 + 主体并发），
一次 pipeline 提交全部 `INCR`，内存判定；任一超限就回滚**本次 INCR 过的全部键**再抛 429。
回滚必须覆盖全部而非「到失败桶为止」：否则主体日桶、场景日桶与并发槽会白扣一个，
并发槽还会因每次重试刷新 300s TTL 把用户锁在「当前对话尚未结束」；回滚到 ≤0 时删键，
避免跨分钟留下无 TTL 的负计数。Redis 往返次数 3 → 1，新增额度不带来额外延迟。

**后台视图**：

- `GET /admin/scenes` 增加 `today_requests` / `quota_daily` / `quota_source`（一条 `GROUP BY scene_code` 聚合）；
- `GET /admin/staff` 返回 `quota` / `used` / `remaining`（3 条 `GROUP BY created_by`，禁止逐人 count）；
- 新增 `admin_staff.py`：列表/新建/PATCH/重置密码，全部 `require_admin`；不能停用、降级自己，不能改自己的配额。

## 关键实现

| 位置 | 变更 |
|---|---|
| `scripts/init_db.sql` | schema v6：`canned_scripts.answer_source/kb_namespaces`、`scenes.runtime_config/created_by`、`agents.created_by`、`knowledge_bases.created_by`、`staff_users.quota`、`idx_chat_events_created_scene` |
| `app/schemas/scene.py` | `SceneRuntime` / `SceneQuota` 冻结数据类（领域快照，热路径直接复用） |
| `app/services/runtime_config.py` | 规范化、知识库校验、`resolve_runtime`、`effective_runtime`、`runtime_effective_dict` |
| `app/services/canned_templates.py` | `TEMPLATE_KEYS`、`ensure_scene_templates`、`backfill_all_scenes` |
| `app/services/canned_kb_answer.py` | kb 话术检索拼装（清洗/去重/预算/超时/降级） |
| `app/services/quota_service.py` | `effective_limits` / `count_owned` / `ensure_can_create` / `create_guard` / `usage_by_owner` |
| `app/agents/orchestrator.py` | 命中话术分流 text/kb；`_run_business_agent` 带 `runtime`；兜底链保留 |
| `app/core/context_engineer.py` | `build(..., runtime=…)`；`rag.inject=false` 跳过自动注入；检索参数来自场景 |
| `app/core/chat_service.py` | `list_recent(limit=runtime.history_limit)` |
| `app/api/v1/admin_scenes.py` | `runtime_config` 读写、默认场景白名单、列表用量、新建时补话术 |
| `app/api/v1/admin_staff.py` | 人员与配额 CRUD（新增） |
| `app/api/v1/admin_router.py` | `no_identity_script` 警告（一次取全表，不看全局行） |
| `app/api/v1/admin_canned_scripts.py` | `answer_source` / `kb_namespaces` 校验与回显 |
| `scripts/backfill_scene_canned.py` | 存量场景幂等补齐（新增） |
| `admin-frontend` | 场景运行参数、今日请求列、固定话术抽屉、话术来源、`StaffQuotaView`、路由页文案 |

## 兼容与升级

项目约定「只保留最新结构」，默认流程是全量重建：

```powershell
cd backend; .\.venv\Scripts\python.exe ..\scripts\rebuild_and_seed.py
cd backend; .\.venv\Scripts\python.exe ..\scripts\backfill_scene_canned.py --dry-run
```

必须保留线上数据时走开发文档附录 A 的 `ALTER TABLE` + 备份，再跑一次 backfill；
`runtime_config` / `quota` 为空、`answer_source=text` 时行为与旧版完全一致。

## 验证

- `python -m pytest -q` → 500 passed, 3 skipped（新增 `test_runtime_config` / `test_quota_service` /
  `test_admin_staff` / `test_canned_kb_answer` / `test_admin_scene_runtime`，并扩展了话术与路由档案用例；
  另含本轮审查修正的回归用例：超限回滚全部桶、话术列表带 `scene_code`、正文置空 400、
  展示名可清空、场景 `rag.namespaces` 优先）；
- `python scripts/verify_schema.py` → `schema_meta.version = 6`，新列与索引齐全；
- `backfill_scene_canned.py --dry-run` 二次执行输出「待补 0 条」（幂等）；
- 前端 `vue-tsc --noEmit` 与 `vite build` 通过。

## 已知边界

- **Agent 级模型配置延后**：`models` 仍由 `.env` + Agent 代码决定，本期不动；
- kb 话术有 100–500ms 延迟（仅 `answer_source=kb` 的行），文本话术零变化；
- 场景日额度用进程本地时间，用量展示用数据库时间，跨时区部署存在一小时边界差异（与现有限流一致）；
- 多进程部署下创建配额存在极小超额窗口，不为低频后台操作改仓储事务边界；
- 探听拒答（`probing`）始终全局生效，不提供场景级关闭。
