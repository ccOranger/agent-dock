# 场景身份话术、运行参数与登录人配额（开发文档）

> 状态：设计已确认，待实施
> 目标 schema：v6
> 关联文档：[U16 固定话术](updates/16-canned-scripts-db.md)、[U20 单租户与埋点](updates/20-single-tenant-degradation.md)、[spec.md](spec.md)
>
> 本文是实施前的开发文档；落地后另起 `docs/updates/21-*.md` 作为发布说明，并同步 `spec.md` / `api.md` / `configuration.md` / `runbook.md`。

---

## 1. 背景与目标

当前平台已经具备多场景路由、场景级固定话术（全局 + 场景覆盖）、Agent 级 `llm_model` 和 Redis 请求限流，但仍存在四个断点：

1. **场景身份不可控**：新建场景不会生成自己的身份话术，客户问“你是谁”时回答的是平台统一文案；话术只做整句/关键词命中，变体问法会落到模型自由发挥；无法用知识库回答身份/介绍类问题。
2. **场景运行参数不可配**：RAG 的 `top_k / min_score / timeout`、上下文历史条数等全部读全局 `Settings`，一个场景的检索策略无法独立调整；路由的意图上限已有字段但后台表单未暴露。
3. **Agent 模型配置不完整**：`agents.llm_model` 只能换模型名，供应商、Base URL、Key、温度仍是全局，且 `llm_factory` 只按模型名缓存。本期确认**整块延后**，见 §2.2。
4. **没有登录人配额**：`scenes / agents / knowledge_bases` 都没有创建人，无法按账号限制创建数量；场景请求额度也只能用全局环境变量，无法按场景单独控制。

本期的目标是把 1、2、4 做完整，并以一次 schema v6 变更落地。

---

## 2. 范围

### 2.1 本期范围

| 编号 | 内容 |
|---|---|
| P1 | 场景化固定话术：场景创建/补齐身份话术、占位符、知识库检索拼接回答（不走 LLM） |
| P2 | 场景运行参数：路由意图上限/模型/阈值补 UI，RAG 自动注入参数与历史条数按场景覆盖 |
| P4 | 登录人资源创建配额 + 场景请求额度（分钟/日）+ 用量展示 + 人员与配额管理页 |

### 2.2 非目标（明确不做）

- **LLM 供应商 / Key / 温度配置**：本期延后。Agent 继续使用 `agents.llm_model`（只换模型名，共用全局凭据）。后续如要做，需要新增 `llm_profiles` 并修改 `llm_factory` 的缓存 key，不能只加字段。
- **多意图并行作答**：多意图仍串行执行；“并发 Agent”只体现为场景可配的 `router_max_intents`，不引入并行 token 流与合并协议。
- **话术命中后再交给 LLM 生成**：知识库模式只做“检索 + 固定框架拼接”，不做改写。
- **正则匹配**：话术匹配仍只开放“问法主干 + 关键词”两档。
- **计费/发票/token 预算**：只做请求次数与资源数量，不做费用结算。
- **按接入方（integration）配额**：`chat_events` 已具备归属数据，留作后续；本期只做登录人与场景总额。
- **记忆开关**：代码 Agent 在 `build_tools` 中无条件注册 memory 工具，场景层无法可靠关闭；为避免提供“看起来能关、实际关不掉”的开关，本期不做。RAG 只提供“自动注入开关”，不承诺关闭模型主动调用工具。
- **文档上传数量配额**：`documents.uploaded_by` 已存在，本期不限制。

---

## 3. 现状与差距（代码定位）

| 能力 | 现状 | 差距 |
|---|---|---|
| 场景话术 | `canned_scripts.scene_id` 支持全局 + 场景覆盖，运行时同 key 场景行优先（`app/agents/canned_scripts.py`、`CannedScriptRepo.list_enabled`） | 建场景不生成身份话术；无占位符；无知识库回答；`no_identity_script` 枚举前端已映射但后端未发出 |
| 话术执行 | 路由前命中即逐字下发（`orchestrator.dispatch` 第 0 步） | 命中后无法做任何异步检索；`load_rules` 会丢弃 `reply_text` 为空的行 |
| 场景参数 | `scenes` 只有路由四项 + fallback + 嵌入配置（`models/entities.py`） | RAG/历史条数读全局；意图上限后台表单未暴露 |
| Agent 模型 | `agents.llm_model` 空则回退 `LLM_MODEL_ID`（`dynamic_agent.py`、`base_business_agent.py`） | 供应商/Key/温度全局；`llm_factory` 只按模型名缓存 |
| 限流 | `acquire_chat_slot(scene, actor)` 三档，全部来自 `Settings`（`core/ratelimit.py`） | 无场景总量桶；无按登录人统计资源的创建人列 |
| 人员 | 只有 seed 的 `staff_users`，无管理接口/页面 | 无法配置配额、无法查看已用数量 |

---

## 4. 总体设计

### 4.1 配置优先级

```
全局环境变量（Settings）
  └─ 场景 runtime_config（未填/null = 继承全局）
       └─ Agent 自身声明（RAG namespace、llm_model）
            └─ 单次请求参数（max_intents 显式入参，内部测试用）
```

话术的覆盖关系保持现状：

```
全局话术行（scene_id IS NULL）
  └─ 场景话术行（scene_id = 本场景；同 key 覆盖全局；场景行停用时不回落到全局）
```

### 4.2 运行时数据流（P1 + P2）

```
resolve_scene
  ├─ 读 scenes + 绑定 Agent → SceneContext
  └─ resolve_runtime(runtime_config) → SceneRuntime（纯函数，无额外查库）

ChatService
  ├─ history_limit ← SceneRuntime
  └─ Orchestrator.dispatch(scene)
       ├─ canned 匹配（全局 + 场景）
       │    ├─ text：渲染占位符后逐字下发
       │    └─ kb：检索知识库 → 固定框架拼接 → 下发（失败回退 text / 继续路由）
       ├─ 路由（意图上限 ← scene.router_max_intents）
       └─ 业务 Agent
            └─ ContextEngineer.build(runtime=SceneRuntime)
                 ├─ rag.inject / top_k / min_score / timeout / namespaces
                 └─ 历史条数由 ChatService 控制
```

### 4.3 设计原则

1. **不新增代码兜底文案**：话术规则与文案只来自 DB；代码只负责匹配、渲染与拼接。
2. **失败降级不阻塞对话**：话术读取、知识库检索、用量统计失败都只告警，本轮继续走正常路由。
3. **热路径不加查库**：`SceneRuntime` 由已加载的 `scenes` 行纯函数解析；话术仍是每轮一条窄查询。
4. **后台看到的 = 运行时用的**：运行参数与路由档案一样，返回 `runtime_config`（原始）与 `runtime_effective`（生效值 + 来源）。
5. **安全优先**：探听拒答话术不提供场景级关闭开关，避免某场景泄露系统提示词。

---

## 5. 数据模型（schema v6）

### 5.1 变更总览

| 表 | 变更 |
|---|---|
| `scenes` | `runtime_config JSONB NOT NULL DEFAULT '{}'`、`created_by UUID` |
| `agents` | `created_by UUID` |
| `knowledge_bases` | `created_by UUID` |
| `staff_users` | `quota JSONB NOT NULL DEFAULT '{}'` |
| `canned_scripts` | `answer_source VARCHAR(16)`、`kb_namespaces JSONB` |
| `schema_meta` | `version = 6` |

### 5.2 字段定义

```sql
-- 场景运行参数：空对象 = 全部继承全局
ALTER TABLE scenes
  ADD COLUMN runtime_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN created_by UUID;

-- 创建人：不建外键，与 audit_logs.user_id 一致，避免人员删除阻塞资源
ALTER TABLE agents ADD COLUMN created_by UUID;
ALTER TABLE knowledge_bases ADD COLUMN created_by UUID;

-- 登录人配额：键缺失或 null = 不限；0 = 禁止创建；正数 = 上限
ALTER TABLE staff_users
  ADD COLUMN quota JSONB NOT NULL DEFAULT '{}'::jsonb;

-- 话术回答来源与知识库范围
ALTER TABLE canned_scripts
  ADD COLUMN answer_source VARCHAR(16) NOT NULL DEFAULT 'text'
      CHECK (answer_source IN ('text','kb')),
  ADD COLUMN kb_namespaces JSONB NOT NULL DEFAULT '[]'::jsonb;

UPDATE schema_meta SET value = '6', updated_at = now() WHERE key = 'version';
```

### 5.3 索引

```sql
CREATE INDEX IF NOT EXISTS idx_scenes_created_by           ON scenes(created_by);
CREATE INDEX IF NOT EXISTS idx_agents_created_by           ON agents(created_by);
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_created_by  ON knowledge_bases(created_by);
-- 后台按日聚合用量：现有 (scene_code, created_at) 前导列是场景，扫不了时间范围
CREATE INDEX IF NOT EXISTS idx_chat_events_created_scene   ON chat_events(created_at DESC, scene_code);
```

`chat_events` 已有 `(scene_code, created_at DESC)`，用于单场景查询；新增的 `(created_at DESC, scene_code)` 服务后台“全场景当日用量”聚合，见 §14.4。

### 5.4 兼容与重建

- 项目默认**不做增量迁移**：`SCHEMA_VERSION` 4 → 5 时也是全量重建。本次升到 6，标准流程仍是：
  ```bash
  python scripts/rebuild_and_seed.py
  ```
- 如需保留现有数据，使用 §15 附录 A 的 ALTER 路径，然后运行 `scripts/backfill_scene_canned.py` 补齐身份话术。
- 旧数据兼容：`runtime_config = {}` 等价于全部继承；`created_by IS NULL` 视为系统资源，不计入任何人的配额；`answer_source` 默认 `text`，行为与现在一致。

---

## 6. 场景运行参数（P2）

### 6.1 配置字典

`runtime_config` 只放**新增**的运行参数；路由模式、路由模型、阈值、意图上限继续使用 `scenes` 现有列，避免同一配置两处维护。

```json
{
  "rag": {
    "inject": true,
    "top_k": 5,
    "min_score": 0.3,
    "timeout": 15,
    "namespaces": ["cs_order", "cs_after_sales"]
  },
  "history_limit": 50,
  "quota": {
    "per_minute": null,
    "daily": 5000
  }
}
```

| 键 | 类型 | 缺省（继承） | 取值范围 | 生效位置 |
|---|---|---|---|---|
| `rag.inject` | bool | `true` | — | `ContextEngineer.build`：是否自动注入知识库上下文 |
| `rag.top_k` | int | `RAG_TOP_K` | 1–20 | 自动注入检索条数 |
| `rag.min_score` | float | `RAG_MIN_SCORE` | 0–1 | 自动注入与话术知识库的最低分 |
| `rag.timeout` | int | `RAG_TIMEOUT` | 1–60 | 自动注入检索超时（秒） |
| `rag.namespaces` | list[str] | `[]` | ≤10，必须是启用中的知识库 | Agent 未声明任何知识库时的兜底检索范围 |
| `history_limit` | int | `50` | 1–50 | `ChatService` 取最近 N 条历史 |
| `quota.per_minute` | int\|null | `0`（不限） | 0–600 | 场景总量分钟桶；0 = 不限 |
| `quota.daily` | int\|null | `0`（不限） | 0–100000 | 场景总量日桶；0 = 不限 |

语义约定：

- `null` / 键缺失 = **继承全局**；`{}` 整对象 = 全部继承。
- `quota.*` 是例外：默认值固定为 `0`（不限），**不继承** `PUBLIC_CHAT_*` 环境变量。原因是两者口径不同——环境变量限的是「单个主体」（user / visitor / ip）每分钟多少次，场景额度限的是「整个场景」所有主体加起来多少次；把 30 次/分 当成场景总额会让多用户场景莫名被限。后台来源标注为 `default` 而不是 `global`。
- `rag.namespaces` 只在 Agent 自身 `rag_namespace(s)` 为空且未开启 `search_all_knowledge_bases` 时生效；Agent 显式声明优先。
- `rag.inject=false` 只关闭自动注入，不阻止模型主动调用 RAG 工具；后台文案必须写清楚。
- 后台表单按“继承 / 覆盖”二态展示，来源字段与路由档案的 `model_source` 语义一致。

### 6.2 校验与解析

新增 `app/services/runtime_config.py`：

| 函数 | 职责 |
|---|---|
| `normalize_runtime_config(payload: dict) -> dict` | 纯函数：白名单键、类型、范围、去重；未知键直接 400，不做静默丢弃 |
| `validate_runtime_namespaces(config) -> None` | 异步：`rag.namespaces` 必须存在且 `is_active` |
| `resolve_runtime(config: dict) -> SceneRuntime` | 纯函数：合并全局默认，产出生效值与来源 |
| `effective_runtime(scene) -> SceneRuntime` | `scene.runtime or SceneRuntime.from_settings()`，兼容手工构造的旧测试快照 |

`SceneRuntime` / `SceneQuota` 数据类放在 `app/schemas/scene.py`（领域快照），解析器放在 service，避免 schemas 依赖 services。

```python
@dataclass(frozen=True)
class SceneQuota:
    per_minute: int
    daily: int
    sources: dict[str, str]  # {"quota.per_minute": "scene"|"default", ...}

@dataclass(frozen=True)
class SceneRuntime:
    rag_inject: bool
    rag_top_k: int
    rag_min_score: float
    rag_timeout: int
    rag_namespaces: tuple[str, ...]
    history_limit: int
    quota: SceneQuota
    sources: dict[str, str]
```

`SceneContext` 增加：

```python
runtime_config: dict = field(default_factory=dict)
runtime: SceneRuntime | None = None
```

`build_scene_context` 填充 `runtime_config` 与 `runtime`；`resolve_scene` 不做额外查询。

### 6.3 接入点

| 位置 | 改动 |
|---|---|
| `chat_service.stream/reply` | `history = MessageRepo.list_recent(..., limit=runtime.history_limit)`；两处共用同一取值 |
| `orchestrator.dispatch` | 取 `effective_runtime(scene)`，传给 `_run_business_agent` / `ContextEngineer.build`；`max_intents` 逻辑不变 |
| `ContextEngineer.build` | 新增 `runtime` 参数；`rag.inject=false` 时跳过 RAG；`_recall_rag` 使用场景 `top_k/min_score/timeout`，`_recall_memory` 使用场景 `timeout` |
| `ContextEngineer._recall_rag` | 增加 `top_k/min_score/timeout` 入参；多库并发仍用现有信号量 4 |
| `RagService.search` | 新增 `timeout: int | None` 参数，默认 `settings.rag_timeout`；不再只能读全局 |
| `base_business_agent` | 不改；RAG 工具仍按 Agent 自身 namespace 注册 |
| `ratelimit.acquire_chat_slot` | 新增 `quota: SceneQuota | None = None`（见 §8.3） |

### 6.4 API 与 UI

- `SceneCreate` / `SceneUpdate` 增加 `runtime_config: dict | None`；PATCH 传了就**整对象替换**，传 `null`/`{}` 表示恢复继承。
- `GET /admin/scenes`、`GET /admin/scenes/{id}` 返回：
  - `runtime_config`：原始配置；
  - `runtime_effective`：解析后的值；
  - `runtime_sources`：每个键的来源（`scene` / `global`）。
- 默认场景：`update_scene` 的只读白名单从 `{display_name, description}` 扩展为 `{display_name, description, runtime_config}`；路由模式、Agent 绑定、fallback 仍只读。
- `ScenesView.vue` 在编辑抽屉增加“高级设置”折叠区：
  - 路由：模型名、置信度阈值、意图上限（复用现有字段，新增输入项）；
  - RAG：自动注入开关、top_k、min_score、timeout、知识库多选；
  - 其他：历史条数；
  - 场景额度：分钟/日（见 P4）。
  - 每个字段带“继承全局（当前值 X）”提示；清空即继承。

---

## 7. 场景化固定话术（P1）

### 7.1 匹配与合并（保持现状）

- `CannedScriptRepo.list_enabled(scene_id)`：全局行 + 本场景行，同 key 场景行覆盖；
- 先合并再过滤 `enabled`，场景行停用时**不回落**全局行；
- `match_rules` 按 `priority` 升序，问法整句归一化全等或关键词包含。

### 7.2 文本模式与占位符

- `answer_source = text`（默认）：命中后渲染占位符，逐字下发，不进路由、不建 Agent。
- 支持占位符：

| 占位符 | 取值 |
|---|---|
| `{{scene_name}}` | 场景展示名（`SceneContext.name`，空则用 code） |
| `{{scene_code}}` | 场景 code |
| `{{agent_names}}` | 本场景绑定 Agent 名称，`、` 分隔 |

- 渲染函数 `render_script_text(text, scene) -> str` 放在 `canned_scripts.py`，纯函数，text 模式与 kb 模式的引导语共用。
- 种子身份文案改为带 `{{scene_name}}`，让每个场景天然有区分：
  > 我是「{{scene_name}}」的智能客服助手，全天候在线，自动为您解答问题。
- `{{}}` 未知占位符保持原样，便于后台测试时发现配置错误。

### 7.3 知识库模式（检索拼接，不走 LLM）

**写入规则**：`answer_source = kb` 时，`kb_namespaces` 至少 1 个，且必须存在、启用；`reply_text` 是可选的引导语 / 兜底文案。

**运行时流程**（新增 `app/services/canned_answer.py`）：

```
async def answer_from_kb(scene, script, runtime, collector) -> str | None
```

1. 过滤 `script.kb_namespaces`：只剩存在且 `is_active` 的库；全部不可用 → 返回 `None`，由调用方回退。
2. 先下发一个状态事件：`{"type":"status","data":{"stage":"preparing","text":"正在查询知识库…"}}`，避免 1–3 秒静默。
3. 并发检索：
   - `RagService.search(ns, query, top_k=3, min_score=runtime.rag_min_score, advanced=False, timeout=min(runtime.rag_timeout, 3))`；
   - 每个库独立 `try/except`，单库失败只告警；全部失败 → `None`；
   - 并发度沿用 4（避免一次性打爆 Qdrant/embedding）。
4. 归并与裁剪：
   - 按 `score` 降序；
   - 按 `content` 归一化去重；
   - 每条折叠空白、截断 180 字（超出加 `…`）；
   - 最多 3 条，总正文 ≤ 800 字；
   - 分数低于 `min_score` 的丢弃。
5. 拼装：
   ```
   intro = render_script_text(reply_text, scene)
   body  = "1. 片段一\n2. 片段二\n3. 片段三"
   answer = intro + "\n\n" + body if intro else body
   ```
6. 返回 `answer`；如果 `body` 为空（零命中）→ 返回 `None`。

**调用方（orchestrator）的降级链**：

```
canned = match_canned_script(...)
if canned:
    rendered = render_script_text(canned.text, scene)
    if canned.answer_source == "text":
        yield rendered; return
    answer = await answer_from_kb(...)
    if answer:
        yield answer; return
    if rendered:          # 知识库无结果，但有兜底文案
        yield rendered; return
    # 两者都空：继续走路由，不拦截本轮
```

**链路记录**：命中 kb 模式时写 `SPAN_CANNED`，字段包含 `category`、`answer_source="kb"`、`kb_namespaces`、`kb_chunks`、`text_chars`；检索失败额外记 `status="error"`。知识库命中计入 `collector.rag_chunk_count`。

**延迟预算**：kb 模式从 0ms 变为约 100–500ms（embedding + Qdrant），硬上限 3 秒。后台字段说明必须标注“比文本话术慢”。

### 7.4 场景初始化与存量补齐

**新建场景**（`admin_scenes.create_scene`）在场景行与 Agent 绑定创建后：

1. 读取全局启用中的 `identity`、`capability` 两行；
2. 若本场景还没有同名 key，复制一份：`scene_id=新场景`、同 `patterns/keywords/reply_text`、`is_builtin=False`；
3. 复制失败只告警，不回滚场景创建（与“自动授权接入方”同一策略）。

**存量补齐**：新增 `scripts/backfill_scene_canned.py`，幂等：

- 确保全局 `identity`/`capability` 存在（不存在则用 `SEED_RULES` 建）；
- 遍历所有场景，缺哪条补哪条；
- 已存在（包括运营改过或停用）一律不覆盖；
- 打印补齐数量；
- `seed_data.py` 在场景创建完成后调用同一函数；
- `scripts/rebuild_and_seed.py` 因为走单次 simple query，不能调 Python 函数，所以用一条等价的幂等 `INSERT ... SELECT ... ON CONFLICT DO NOTHING`（`WHERE c.scene_id IS NULL AND c.key IN ('identity','capability')`）补到每个场景，规则与函数版本完全一致。

**为什么不覆盖**：场景行一旦生成就代表运营配置，重复 seed 不能把运营改过的文案改回模板。

### 7.5 后台警告

- `no_identity_script` 只在**后台路由档案**发出，不进入运行时快照（避免热路径多一次查询）。
- `admin_router.py` 的 `router_scenes` / `router_scene_detail` 在 `profile_to_dict` 之后补一条：
  - 一次取全表并按场景归组「本场景自有且 `enabled` 的 key」（全局行不计）；
  - 本场景没有 `identity` → 追加 `no_identity_script`；
  - 只看场景行是故意的：继承全局文案正是要提醒的事，全局行存在也不算“已配置”。
- 前端文案从“未配置身份/能力话术”改为“未配置本场景身份话术，当前继承全局文案”。

### 7.6 API 与 UI

**Schema**（`app/schemas/api.py`）：

```python
class CannedScriptCreate(BaseModel):
    ...
    answer_source: Literal["text", "kb"] = "text"
    kb_namespaces: list[str] = []

class CannedScriptUpdate(BaseModel):
    ...
    answer_source: Literal["text", "kb"] | None = None
    kb_namespaces: list[str] | None = None
```

**校验**（`admin_canned_scripts.py`）：

- `answer_source=kb` 时 `kb_namespaces` 去重后不能为空；
- 每个 namespace 必须存在于 `knowledge_bases` 且 `is_active`；
- 全局话术（`scene_code` 为空）同样允许 kb，但必须显式选库；
- 切回 `text` 时保留 `kb_namespaces` 不报错（便于来回切换），运行时忽略。

**接口**：`GET/POST/PATCH /admin/canned-scripts` 的返回与入参增加上述两个字段；命中测试接口返回 `answer_source` 与 `kb_namespaces`。

**UI**：

- `CannedScriptsView.vue`：
  - 新增“回答来源”单选（文本 / 知识库）；
  - 来源为知识库时展示知识库多选（复用 `apiListKnowledgeBases`）与提示“检索结果会按固定格式拼接，不经过模型改写”；
  - 场景筛选已有，保持不变。
- `ScenesView.vue`：
  - 场景行操作增加“固定话术”入口，打开抽屉并按 `scene_code` 过滤；
  - 抽屉提供“从模板生成本场景身份/能力话术”按钮（调用现有创建接口，key 为 `identity`/`capability`）；
  - 没有本场景身份行时展示提示，引导一键生成。
- `RouterInfoView.vue`：更新 `no_identity_script` 文案。

### 7.7 边界

- kb 模式不做 LLM 改写，知识库条目建议写成第一人称问答，否则拼接结果会像文档摘录。
- kb 模式不替代硬话术：高频、措辞必须逐字一致的“你是谁”仍建议用 text；kb 适合“公司介绍/服务范围/资质”这类会变的问答。
- 检索失败、超时、零命中都回退，不向客户抛错。
- 探听拒答（`probing`）始终生效，不提供场景级关闭。

---

## 8. 登录人配额与场景请求额度（P4）

### 8.1 配额语义

| 值 | 含义 |
|---|---|
| 键缺失 / `null` | 不限 |
| `0` | 禁止创建 |
| `> 0` | 上限 |

- 只统计 `created_by = 当前登录人` 的资源；
- `created_by IS NULL` 的资源（默认场景、seed 的代码 Agent、演示数据）不计入任何人；
- 删除资源立即释放额度；编辑不消耗；归档场景仍占用额度（行还在）；
- 新账号默认 `quota = {}`，即不限；需要限制时由管理员显式配置；
- 管理员自身默认也不受限，但可以被另一个管理员配置额度。

`staff_users.quota` 本期键：

```json
{
  "max_scenes": 1,
  "max_agents": 2,
  "max_knowledge_bases": 2
}
```

键可扩展；写入时拒绝未知键与负数。

### 8.2 资源创建配额

新增 `app/services/quota_service.py`：

| 函数 | 职责 |
|---|---|
| `effective_limits(user) -> dict` | 读取 `user.quota`，缺失键返回 `None` |
| `count_owned(user_id, kind) -> int` | 按表 count `created_by` |
| `ensure_can_create(user, kind) -> None` | 超限抛 `QuotaExceeded` |
| `staff_usage(user_id) -> dict` | 返回三种资源的已用数量 |

- `kind`：`scene` / `agent` / `knowledge_base`，与表一一对应。
- 并发处理：进程内按 `(user_id, kind)` 的 `asyncio.Lock` 串行化校验与创建调用；多进程部署仍存在极小超额窗口，**接受**（后台创建是低频人工操作，不影响对话）。不为此改仓储的事务边界。
- 审计：拒绝时写 `audit_logs(action="quota.reject", target_type=kind, payload={"limit":..., "used":...})`。

**接入点**：

| 接口 | 写入 |
|---|---|
| `POST /admin/scenes` | `created_by=user.id` |
| `POST /admin/agents` | `created_by=user.id` |
| `POST /knowledge/bases` | `created_by=user.id` |

- 代码 Agent（seed / 注册表）不经过该接口，`created_by` 为 NULL。
- 错误码：新增 `QuotaExceeded`（code `3006`，HTTP 403），文案示例：
  - `场景创建数量已达上限（1/1），请删除不再使用的场景或联系管理员调整配额`
  - `Agent 创建数量已达上限（2/2）…`
  - `知识库创建数量已达上限（2/2）…`

### 8.3 场景请求额度

`acquire_chat_slot(scene_code, actor, quota: SceneQuota | None = None)`：

| 检查顺序 | 桶 | Key | TTL | 说明 |
|---|---|---|---|---|
| 1 | 主体分钟 | `rl:chat:m:{scene}:{actor}:{bucket}` | 120 | 现有 |
| 2 | 主体日 | `rl:chat:d:{scene}:{actor}:{day}` | 172800 | 现有 |
| 3 | **场景分钟** | `rl:chat:sm:{scene}:{bucket}` | 120 | 新增 |
| 4 | **场景日** | `rl:chat:sd:{scene}:{day}` | 172800 | 新增 |
| 5 | 主体并发 | `rl:chat:c:{scene}:{actor}` | 300 | 现有，场景配额不新增并发桶 |

- 限值来源：`quota` 参数（场景覆盖）优先；未配置时为 `0`（不限），**不回落到全局 `Settings`**（两者对象不同，见 §6.1）。
- `0` = 不限（与现有环境变量语义一致）。
- 超限统一抛 `RateLimited`（4029/429），文案区分：
  - `本场景今日对话额度已用完（上限 5000 次），请联系管理员`
  - `本场景请求过于频繁，请稍后再试（每分钟上限 60 次）`
- Redis 故障保持 **fail-open**：放行并每分钟告警一次，与现有限流一致。
- 被拒绝的请求也已经在 `chat_events` 留痕（埋点在限流之前），因此用量统计包含被限流的请求。
- 主体并发槽位仍只由 `ChatSlot` 管理；场景配额不改变释放逻辑。

**接入点**：`chat.py` 的 `chat_stream` / `chat_message` 调用 `acquire_chat_slot(scene.code, actor, scene.runtime.quota if scene.runtime else None)`。

### 8.4 用量统计与展示

**场景列表**（`GET /admin/scenes`）新增：

- `today_requests`：`chat_events` 中本场景当日请求数；
- `quota_daily`：生效的日额度（场景覆盖，未配置为 `0` = 不限）；
- `quota_source`：`scene` / `default`。

实现：

- `ChatEventRepo.count_by_scene_today() -> dict[str, int]`，一条 `GROUP BY scene_code`；
- `since` 取进程本地时区当日 00:00；
- 现有 `(scene_code, created_at DESC)` 的前导列是场景，按时间范围过滤退化为全表扫描；本期补建 `idx_chat_events_created_scene (created_at DESC, scene_code)`（见 §5.3）；
- 只影响后台列表接口，不在对话热路径。

**人员列表**（`GET /admin/staff`）新增每人：

- `used: {scenes, agents, knowledge_bases}`；
- `quota`；
- `remaining`（`null` 表示不限）。

**已知边界**：日额度桶用进程本地时间（`time.strftime`），用量展示用数据库时间；跨时区部署可能有一小时的边界差异，与现有限流行为一致，不在本期修正。

### 8.5 人员与配额管理

新增 `app/api/v1/admin_staff.py`（全部 `require_admin`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/staff` | 人员列表 + 配额 + 已用数量 |
| POST | `/admin/staff` | 新建人员（username 唯一、password ≥ 8、role ∈ {admin, agent}） |
| PATCH | `/admin/staff/{id}` | 修改 display_name / role / is_active / quota |
| POST | `/admin/staff/{id}/password` | 重置密码（可选，建议本期一并做） |

保护规则：

- 不能停用、降级自己；
- 不能修改自己的 quota（避免把自己锁死）；
- 不提供物理删除，只停用；
- 所有写操作写 `audit_logs`。

`StaffRepo` 新增 `list_all`、`update`、`set_password`。

### 8.6 前端

- 新增 `StaffQuotaView.vue`（菜单“人员与配额”，仅 admin）：
  - 表格列：用户名、角色、启用、场景已用/上限、Agent 已用/上限、知识库已用/上限；
  - 编辑弹窗：三个配额输入（留空 = 不限，0 = 禁止）；
  - 新建人员弹窗；
  - 不能编辑自己配额时禁用并提示。
- `ScenesView.vue` 列表增加“今日请求 / 日额度”列；超 80% 标黄，达到上限标红。
- `api/index.ts`、`types/index.ts`、`router/index.ts`、`AdminLayout.vue` 同步新增页面与类型。

---

## 9. 接口变更清单

### 9.1 新增

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/staff` | 人员 + 配额 + 已用 |
| POST | `/admin/staff` | 新建人员 |
| PATCH | `/admin/staff/{id}` | 修改人员/配额 |
| POST | `/admin/staff/{id}/password` | 重置密码 |

### 9.2 变更

| 端点 | 变更 |
|---|---|
| `POST /admin/scenes` | 接收 `runtime_config`；写 `created_by`；创建后复制身份/能力话术 |
| `PATCH /admin/scenes/{id}` | 接收 `runtime_config`；默认场景允许改 `runtime_config` |
| `GET /admin/scenes` | 返回 `runtime_config`、`today_requests`、`quota_daily`、`quota_source` |
| `GET /admin/scenes/{id}` | 返回 `runtime_config`、`runtime_effective`、`runtime_sources` |
| `GET /admin/scenes/options` | 不变 |
| `GET/POST/PATCH /admin/canned-scripts` | 新增 `answer_source`、`kb_namespaces` |
| `POST /admin/agents` | 写 `created_by`；创建前校验 Agent 配额 |
| `POST /knowledge/bases` | 写 `created_by`；创建前校验知识库配额 |
| `GET /admin/router/scenes`、`/scenes/{code}` | 追加 `no_identity_script` 警告 |
| `POST /chat/stream`、`POST /chat/message` | 限流增加场景分钟/日桶 |

### 9.3 错误码

| code | HTTP | 场景 |
|---|---|---|
| `3006` | 403 | 资源创建配额已满（新增 `QuotaExceeded`） |
| `4029` | 429 | 主体或场景请求额度超限（复用 `RateLimited`） |

---

## 10. 关键实现文件

### 10.1 后端

| 文件 | 变更 |
|---|---|
| `scripts/init_db.sql` | 五表字段、索引、schema 版本 6 |
| `scripts/seed_data.py` | 身份文案改用占位符；调用场景话术补齐 |
| `scripts/backfill_scene_canned.py` | 新增：存量场景幂等补齐身份/能力话术 |
| `backend/app/core/schema.py` | `SCHEMA_VERSION = "6"` |
| `backend/app/core/exceptions.py` | `QuotaExceeded`（3006/403） |
| `backend/app/models/entities.py` | 新字段 |
| `backend/app/models/repositories.py` | `SceneRepo`/`AgentRepo`/`KnowledgeBaseRepo` 写 `created_by`；`StaffRepo` 管理方法；`ChatEventRepo.count_by_scene_since`；`CannedScriptRepo` 新增克隆/查询辅助 |
| `backend/app/schemas/scene.py` | `SceneRuntime`、`SceneQuota`、`SceneContext` 新字段 |
| `backend/app/schemas/scene_admin.py` | `runtime_config` 入参/出参 |
| `backend/app/schemas/api.py` | 话术字段、Staff 管理模型 |
| `backend/app/services/runtime_config.py` | 新增：规范化、校验、解析 |
| `backend/app/services/quota_service.py` | 新增：创建配额与用量 |
| `backend/app/services/canned_answer.py` | 新增：知识库检索拼接 |
| `backend/app/services/scene_service.py` | 装配 `runtime_config` / `runtime` |
| `backend/app/services/chat_service.py` | 历史条数取场景值 |
| `backend/app/agents/canned_scripts.py` | 占位符、`answer_source`、`kb_namespaces`、`load_rules` 过滤修正 |
| `backend/app/agents/orchestrator.py` | kb 话术降级链；传 runtime 给上下文工程 |
| `backend/app/core/context_engineer.py` | RAG 注入开关与场景参数 |
| `backend/app/services/rag_service.py` | `search` 支持 timeout |
| `backend/app/core/ratelimit.py` | 场景分钟/日桶 |
| `backend/app/api/v1/chat.py` | 传入场景配额 |
| `backend/app/api/v1/admin_scenes.py` | 运行参数、created_by、配额校验、话术克隆、默认场景白名单 |
| `backend/app/api/v1/admin.py` | Agent 创建配额与 created_by |
| `backend/app/api/v1/knowledge.py` | 知识库创建配额与 created_by |
| `backend/app/api/v1/admin_canned_scripts.py` | 新字段校验与返回 |
| `backend/app/api/v1/admin_router.py` | 身份话术警告 |
| `backend/app/api/v1/admin_staff.py` | 新增人员管理 |

### 10.2 前端（admin-frontend）

| 文件 | 变更 |
|---|---|
| `src/types/index.ts` | `SceneRuntimeConfig`、`SceneRuntimeView`、`CannedAnswerSource`、`StaffUserWithUsage`、`StaffQuota` |
| `src/api/index.ts` | 新端点与字段 |
| `src/views/ScenesView.vue` | 高级设置、话术抽屉、用量列 |
| `src/views/CannedScriptsView.vue` | 回答来源、知识库多选 |
| `src/views/StaffQuotaView.vue` | 新增 |
| `src/views/RouterInfoView.vue` | 警告文案 |
| `src/router/index.ts` / `src/layouts/AdminLayout.vue` | 新页面与菜单 |

---

## 11. 测试计划

### 11.1 单元测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_canned_scripts.py` | 占位符渲染；`answer_source=kb` 且 `reply_text` 为空的行不被 `load_rules` 丢弃；场景覆盖优先级不变 |
| `tests/test_canned_answer.py`（新增） | kb 命中拼接、去重、截断、分数过滤；零命中/超时/异常回退；单库失败不影响其他库；trace 字段 |
| `tests/test_scene_runtime_config.py`（新增） | 白名单/范围/未知键 400；继承与覆盖；来源字段；Agent 显式 namespace 优先；`rag.inject=false` 不检索 |
| `tests/test_staff_quota.py`（新增） | null/0/正数语义；三种资源超限；删除释放；`created_by IS NULL` 不计入；拒绝审计 |
| `tests/test_scene_quota.py`（新增） | 场景分钟/日桶；`0` 不限；Redis 失败放行；主体桶仍生效；文案与 4029 |
| `tests/test_scenes.py` | 默认场景允许改 `runtime_config`、仍禁止改路由/绑定 |
| `tests/test_admin_canned_scripts.py` | kb 模式缺 namespace 报错；namespace 不存在/停用报错；切回 text 保留配置 |
| `tests/test_admin_router.py` | 缺场景身份话术时返回 `no_identity_script` |
| `tests/test_knowledge_bases.py` / `test_admin_router.py` | 创建写 `created_by`、配额校验接入 |

### 11.2 手工验收

1. 建两个场景，分别把身份话术改成不同文案；同一句“你是谁”在两个场景返回不同内容。
2. 场景身份话术用 kb 模式绑定一个知识库；命中返回片段；停用该库后回退 `reply_text`；两者都空时继续正常路由。
3. 场景日额度设 2，第三次请求返回 4029 与明确文案；次日恢复（或清 Redis key 验证）。
4. 登录人配额：场景上限 1，建第二个返回 403；删除第一个后可再建。
5. 默认场景可改 `runtime_config`（限流/RAG），但路由与绑定仍不可改。
6. `python scripts/rebuild_and_seed.py` 后，所有场景都有本场景身份/能力话术；重复执行不覆盖运营修改。
7. 后台场景列表显示今日请求/额度；人员页显示已用/上限。

### 11.3 回归

- 现有 `tests/test_canned_scripts.py`、`test_scenes.py`、`test_scene_router_profile.py`、`test_router.py`、`test_timeout_guards.py` 全绿；
- 匿名嵌入与服务端 Key 两条聊天链路不受限流新增桶影响；
- `npm run build`（admin-frontend）通过。

---

## 12. 实施顺序与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1-1 | schema v6 字段、实体、`load_rules` 修正、占位符 | 单测通过；旧话术行为不变 |
| P1-2 | 场景创建/seed 补齐身份话术、后台警告 | 新场景自动有身份行；重复 seed 不覆盖 |
| P1-3 | kb 模式检索拼接、trace、UI | 手工验收 2 通过；失败可回退 |
| P2-1 | `SceneRuntime` + 解析器 + 校验 | 单测覆盖继承/覆盖/非法值 |
| P2-2 | ContextEngineer / ChatService / RagService 接入 | 场景 top_k/timeout/history 生效；Agent 显式 namespace 优先 |
| P2-3 | 场景表单高级设置、意图上限/模型/阈值 UI | 界面显示继承/覆盖；默认场景限制正确 |
| P4-1 | `created_by`、`staff_users.quota`、QuotaService | 三种资源超限返回 3006；删除释放 |
| P4-2 | 场景分钟/日额度、Redis 桶、文案 | 手工验收 3 通过；Redis 故障放行 |
| P4-3 | 人员与配额页、场景用量列 | 手工验收 7 通过 |
| 收尾 | 全量测试、重建库、文档同步 | 全绿；`verify_schema.py` 通过 |

每阶段单独提交，P1 完成后即可独立上线验证；P2/P4 可以合并发布，但 schema 只升一次 v6。

---

## 13. 风险与回滚

| 风险 | 处理 |
|---|---|
| schema v6 需全量重建，线上数据会丢 | 默认流程仅用于开发/演示；生产走附录 A 的 ALTER + backfill，并先备份 |
| kb 话术增加 100–500ms 延迟 | 硬超时 3 秒、top_k=3、`advanced=False`；后台字段说明标注延迟；失败立即回退 |
| 场景额度把正常客户挡在门外 | 默认 `0`（不限），**不继承** `PUBLIC_CHAT_*`；只有管理员显式配置才生效；超限文案给出管理员动作 |
| Redis 故障导致额度失效 | 与现有限流一致 fail-open；需要硬额度时再单独评估 fail-closed |
| 并发创建绕过配额 | 进程内锁 + 接受多进程极小超额；后台低频操作，不为此改仓储事务 |
| 存量话术被 seed 覆盖 | backfill 只补缺失，不覆盖已有行 |
| 默认场景被误改运行参数 | 只开放 `runtime_config`，路由/Agent/fallback 仍只读；写审计 |
| 回滚 | 代码回滚 + 恢复备份库；`runtime_config`/`quota` 为空时行为与旧版一致，`answer_source` 默认 text |

---

## 14. 性能影响评估

结论：**对话热路径的增量是毫秒级**——P1 文本话术 +0、P2 +0（不加查库）、P4 多两次 Redis 往返（实施时合并为一次，见 §14.2）。唯一有感知的延迟只有“知识库型话术”本身（100–500ms，仅命中 `answer_source=kb` 时）。真正的成本在后台列表接口与 Redis 往返次数上，下面三条是实施强制项。

### 14.1 逐项预算

| 改动 | 触发位置 | 增量 | 说明 |
|---|---|---|---|
| 占位符渲染 | 话术命中 | ~µs | 纯字符串替换，每轮最多一次 |
| kb 话术检索 | 仅 `answer_source=kb` 的行 | +100–500ms，硬上限 3s | 文本话术零变化 |
| `SceneRuntime` 解析 | 每轮 | ~µs，**0 次查库** | 用已加载的 `scenes` 行做纯函数解析 |
| `history_limit` | 每轮 | ≤ 现状 | 只会减少 `list_recent` 返回行数 |
| RAG `top_k/min_score/timeout` | 每轮 | 0 | 只是参数替换；场景配小反而更快 |
| 场景分钟/日额度 | 每轮 | +2 次 Redis 往返 | 见 §14.2，实施时合并 |
| 创建配额校验 | 后台创建 | 3 次 `created_by` count | 索引列，人工低频 |
| 场景用量统计 | `GET /admin/scenes` | 1 次按日聚合 | 见 §14.3 / §14.4 |
| staff 已用数量 | `GET /admin/staff` | 3 次分组聚合 | 禁止按人循环，见 §14.3 |
| schema v6 新列 | 存储 | 每行几十字节 | JSONB 默认 `{}`/`[]`，不膨胀索引 |

### 14.2 强制项一：限流桶合并为一次 Redis 往返

现状 `acquire_chat_slot` 已有 3 次独立 `_incr`（分钟 / 日 / 并发），每次都是 `pipeline + execute`。P4 再加 2 个场景桶会变成 5 次往返：本机约 0.2–1ms/次，跨可用区可达 1–3ms/次，直接推迟首字节。

P4-2 实施要求：

1. 一次 pipeline 提交全部 5 个 `INCR` + `EXPIRE`，在内存里按返回计数判定；
2. 任一超限时，只对**本次已自增**的键 `DECR` 回滚，再抛 `RateLimited`（否则用户被扣次数却收到 429）；
3. `ChatSlot` 的并发释放语义不变（仍然只释放并发桶）；
4. 结果：Redis 往返 3 → 1，**比现状更快**，新增额度不带来额外延迟；
5. 单测必须覆盖“超限后计数回滚”。

### 14.3 强制项二：后台统计禁止 N+1

| 接口 | 禁止写法 | 必须写法 |
|---|---|---|
| `GET /admin/scenes` | 每个场景查一次 `chat_events` | 1 条 `WHERE created_at >= :since GROUP BY scene_code` |
| `GET /admin/staff` | 每个人员 3 次 count | 3 条 `GROUP BY created_by`，内存合并 |
| `GET /admin/router/scenes` | 每个场景查一次 identity 话术 | 1 条 `SELECT DISTINCT scene_id ... key='identity' AND enabled` |

`GET /admin/router/scenes` 现在已经是逐场景 `load_scene_context` 的 N+1，P1 加身份话术警告时**不得**再叠一层循环查询；缺失判定用一条批量查询后在内存比对。

### 14.4 强制项三：`chat_events` 按时间聚合的索引

现有 `idx_chat_events_scene_created` 是 `(scene_code, created_at DESC)`，前导列是场景，无法高效支撑 `WHERE created_at >= since GROUP BY scene_code`（只能扫全索引）。新增：

```sql
CREATE INDEX IF NOT EXISTS idx_chat_events_created_scene
    ON chat_events(created_at DESC, scene_code);
```

只服务后台列表；对话侧多维护一个索引的写入代价，远小于后台按日聚合的扫描代价。

### 14.5 明确无影响的部分

- 前端新增列 / 抽屉：只影响后台首屏 DOM，知识库列表按需加载；
- `runtime_config` / `quota` 写入：JSONB 小对象，行宽与读写开销可忽略；
- 话术读取仍是每轮一条窄查询（`canned_scripts` 有 `(scene_id, key)` 唯一约束）；
- Redis 新增键：每场景每分钟 1 个、每天 1 个，量级可忽略；
- 计费、token 预算、按接入方配额均不做，不引入额外链路。

---

## 15. 附录 A：保留数据的升级路径（非项目默认流程）

> 项目约定是“只保留最新结构，换库全量重建”。以下 SQL 仅用于必须保留线上数据的场景；执行前备份。

```sql
BEGIN;

ALTER TABLE scenes
  ADD COLUMN IF NOT EXISTS runtime_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS created_by UUID;

ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS created_by UUID;

ALTER TABLE knowledge_bases
  ADD COLUMN IF NOT EXISTS created_by UUID;

ALTER TABLE staff_users
  ADD COLUMN IF NOT EXISTS quota JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE canned_scripts
  ADD COLUMN IF NOT EXISTS answer_source VARCHAR(16) NOT NULL DEFAULT 'text',
  ADD COLUMN IF NOT EXISTS kb_namespaces JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE canned_scripts
  ADD CONSTRAINT ck_canned_scripts_answer_source
  CHECK (answer_source IN ('text','kb'));

CREATE INDEX IF NOT EXISTS idx_scenes_created_by          ON scenes(created_by);
CREATE INDEX IF NOT EXISTS idx_agents_created_by          ON agents(created_by);
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_created_by ON knowledge_bases(created_by);
CREATE INDEX IF NOT EXISTS idx_chat_events_created_scene  ON chat_events(created_at DESC, scene_code);

UPDATE schema_meta SET value = '6', updated_at = now() WHERE key = 'version';

COMMIT;
```

然后执行：

```bash
python scripts/backfill_scene_canned.py
```

该脚本只补缺失的场景身份/能力话术，不覆盖已有配置，可重复执行。

---

## 16. 文档与运维同步

| 文档 | 动作 |
|---|---|
| `docs/spec.md` | 更新 schema、场景运行参数、配额章节 |
| `docs/api.md` | 新增/变更端点与错误码 3006 |
| `docs/configuration.md` | 补充 `runtime_config` 字典与配额语义 |
| `docs/runbook.md` | 增加“场景额度耗尽”“配额拒绝”“身份话术未配置”排查 |
| `docs/updates/README.md` | 落地后新增 U21 索引 |
| `docs/updates/21-scene-identity-runtime-quota.md` | 落地后写发布说明（当前文档不替代） |

---

## 17. 实施检查清单

- [x] `scripts/init_db.sql` 字段、索引、schema v6
- [x] `SCHEMA_VERSION = 6`
- [x] `canned_scripts` 新字段与 `load_rules` 过滤修正
- [x] 占位符渲染 + 种子文案改造
- [x] 场景创建/backfill 身份话术
- [x] kb 话术检索拼接、超时、回退、trace
- [x] `SceneRuntime` 数据类、规范化、校验、解析
- [x] `SceneContext` 装配 runtime
- [x] `ContextEngineer` / `RagService` / `ChatService` 接入
- [x] 场景 API 的 `runtime_config` 与默认场景白名单
- [x] 场景表单高级设置与话术抽屉
- [x] `created_by` 三表与 `staff_users.quota`
- [x] `QuotaService` 与 3006
- [x] 场景分钟/日额度与 Redis 桶
- [x] 限流桶合并为单次 Redis 往返 + 超限回滚
- [x] `chat_events(created_at DESC, scene_code)` 索引
- [x] 后台三个列表接口批量查询，无 N+1
- [x] 人员与配额页
- [x] 场景列表用量列
- [x] 路由页 `no_identity_script`
- [x] 单元测试与手工验收
- [x] 重建库 + `verify_schema.py`
- [x] 文档同步
