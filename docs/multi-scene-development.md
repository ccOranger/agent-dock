# 多场景平台化开发文档

> 目标：一套代码、一个聊天入口，通过 `scene_code` 服务不同业务场景；新场景全部后台配置，不再改代码。
> 状态：设计已确认，进入实现。
> 关联文档：[API 契约](./api.md)、[配置说明](./configuration.md)、[部署](./deployment.md)、[运行手册](./runbook.md)。
> 约束：不做数据迁移。结构变更后重建空库，以初始化结果验证初始化脚本。

---

## 1. 背景与目标

当前系统已经具备多场景所需的大部分“零件”：

- 知识库创建与向量化、MCP 创建与探测、动态 Agent CRUD 与工具绑定；
- 运行时按库中 Agent 实时拼路由提示词、多意图串行、追问续接；
- 固定话术、链路追踪。

缺的是“场景”这一层：Agent、知识库、话术、会话、记忆、链路都没有场景维度，路由是进程级单例。结果是：

- 新增业务仍要改代码或改 `.env`；
- 两套业务同时接入时，路由、话术、知识库会互相串；
- 对外只有一个强依赖客户 JWT 的入口，第三方页面无法嵌入。

本期目标：

1. 后台创建场景，绑定若干 Agent（动态 Agent 为主，代码 Agent 可选）；
2. 一个 `/chat/stream`，用 `scene_code` 区分场景；
3. 无 `scene_code` 时走默认场景：单个写死代码 Agent + 单个默认知识库，直连不路由；
4. 每个场景拥有独立的路由档案、话术、知识库范围、会话、记忆、链路；
5. 提供 API 对接与“一行 script”嵌入两种方式；
6. 全量重建数据库，验证初始化脚本可重复执行。

---

## 2. 核心决策

| 决策 | 结论 | 理由 / 被否方案 |
|---|---|---|
| 默认场景 | 单个代码 Agent `default_assistant` + 默认知识库，`direct` 直连 | 默认场景是“产品底座”，不应依赖动态配置；绑定全部代码 Agent 会让默认行为随内置能力变化而漂移 |
| 场景码 | `scenes.code` 全局唯一、对外公开、创建后不可改 | 它是嵌入配置的一部分，改了会打断所有已发布页面 |
| 错码 / 停用码 | 返回 404（3003/3004），不静默回落默认场景 | 静默回落会让第三方以为“接的是自己的场景”，实际答的是默认知识 |
| 路由实体 | 不写 `agents` 表；每个场景一份派生“路由档案” | 写进 agents 会被业务解析、MCP 反查、链路统计误当成业务 Agent |
| 路由模式 | `auto`（0 个=未配置、1 个=直连、≥2 个=LLM）、`llm`、`direct` | 单 Agent 场景强制走 LLM 只会增加一次往返和两个失败面 |
| Agent 命名 | `UNIQUE NULLS NOT DISTINCT (scene_id, name)` | 场景内可同名；全局名不必加前缀；`scene_id NULL` 表示全局共享 |
| 代码 Agent | 作为“内置代码能力”保留，可被场景绑定；新业务默认只建动态 Agent | 工具和复杂逻辑仍用代码写，避免把代码能力硬塞进 JSON 提示词 |
| 话术 | `probing` 全局；`identity/capability` 场景级 | 身份与能力描述必须随业务变化，探听配置可以共用 |
| 知识库 | `knowledge_bases.scene_id`，NULL=平台共享；`search_all` 只在本场景内生效 | 全局“检索全部启用库”是最大的串场风险 |
| 记忆 | `actor_key = user:<uuid> | visitor:<uuid>`，按场景隔离 | 只按 user_id 隔离无法支持匿名访客，也会跨场景串记忆 |
| 会话 | `sessions.scene_id NOT NULL` + user/visitor 二选一 | 会话是场景内的对话，跨场景复用 session_id 必须拒绝 |
| 工具/MCP | 名称保持全局唯一，`tools.scene_id` 标记归属；场景只能绑本场景或全局工具 | 首期不引入工具名作用域，降低绑定解析复杂度 |
| 初始化 | `init_db.sql` 是唯一 DDL 源；`seed_data.py`/`app.setup` 不再 `create_all` | 三套 DDL 源必然漂移，“验证初始化脚本”会变成验证其中一条路径 |
| 统计 | `request_traces` 拍平 `router_skipped/router_fallback/router_ms` | 列表页聚合不能逐条拆 `spans` JSONB |

---

## 3. 术语

| 术语 | 含义 |
|---|---|
| 场景 Scene | 一套对外业务，拥有自己的 Agent 池、路由配置、话术、知识库范围和嵌入配置 |
| 场景码 scene_code | 对外唯一标识，例如 `qqq111111`；嵌入代码和 API 调用都带它 |
| 默认场景 | `is_default=true` 的场景，无码请求的落点；只读、不可停用/删除/解绑 |
| 路由档案 RouterProfile | 由场景配置 + Agent 池 + 全局模板派生出的一份只读视图，运行时与后台共用 |
| 直连 direct | 池内恰好 1 个 Agent，跳过路由 LLM，置信度视为 1.0 |
| Actor | 会话/记忆的归属者，`user:<uuid>` 或 `visitor:<uuid>` |

---

## 4. 总体架构

```
第三方页面 / 自有前端 / API 调用方
                │  scene_code（可缺省）
                ▼
        ┌───────────────────┐
        │  SceneResolver     │  无码→default；错码/停用→404
        └─────────┬─────────┘
                  ▼
        ┌───────────────────┐
        │  ChatService       │  会话(scene+actor)、消息、链路
        └─────────┬─────────┘
                  ▼
        ┌───────────────────┐
        │  Orchestrator      │  固定话术 → 路由档案 → 分发
        └─────────┬─────────┘
          ┌───────┴────────┐
          ▼                ▼
   direct（1 个 Agent）  llm（≥2 个 Agent）
   跳过路由 LLM           RouterAgent + 场景提示词
          └───────┬────────┘
                  ▼
        ┌───────────────────┐
        │  业务 Agent        │  只允许本场景 Agent 池
        │  RAG / Memory / MCP│  知识库、记忆按场景收敛
        └───────────────────┘
```

关键原则：

- **场景是隔离边界**：路由候选、知识库、话术、会话、记忆、链路都必须带场景。
- **路由是配置不是实体**：`router:{scene_code}` 只是展示身份，不进入 `agents` 表。
- **运行时与后台同源**：`build_scene_router_profile()` 同时服务路由执行和后台查看，避免“看到的和跑的不一样”。

---

## 5. 数据模型

### 5.1 新增表

#### `scenes`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | 场景 ID；默认场景使用固定 UUID |
| `code` | VARCHAR(32) UNIQUE NOT NULL | 对外场景码，创建后不可改 |
| `display_name` | VARCHAR(64) NOT NULL | 后台/组件展示名 |
| `description` | TEXT | 用途说明 |
| `status` | VARCHAR(16) NOT NULL DEFAULT 'active' | `active/paused/archived` |
| `is_default` | BOOLEAN NOT NULL DEFAULT false | 部分唯一索引保证只有一个 |
| `router_mode` | VARCHAR(16) NOT NULL DEFAULT 'auto' | `auto/llm/direct` |
| `router_model` | VARCHAR(64) NULL | NULL=继承全局 |
| `router_confidence_threshold` | DOUBLE PRECISION NULL | NULL=继承全局 |
| `router_max_intents` | INTEGER NULL | NULL=继承全局 |
| `fallback_agent_id` | UUID NULL | 不建外键，应用层校验；避免与 agents.scene_id 循环 |
| `fallback_text` | TEXT NULL | 场景级兜底话术 |
| `extra_router_prompt` | TEXT NULL | 场景补充识别规则 |
| `widget_config` | JSONB NOT NULL DEFAULT '{}' | 标题、欢迎语、主题、位置等 |
| `allowed_origins` | JSONB NOT NULL DEFAULT '[]' | 嵌入允许来源 |
| `publish_key` | VARCHAR(64) NULL | 前端嵌入公开标识（非密钥） |
| `metadata` | JSONB NOT NULL DEFAULT '{}' | 扩展位 |
| `created_at/updated_at` | TIMESTAMPTZ | 时间戳 |

约束与索引：

- `UNIQUE (code)`
- `CREATE UNIQUE INDEX ... ON scenes (is_default) WHERE is_default`（PG 部分索引）
- `INDEX (status)`

#### `scene_agents`

| 列 | 类型 | 说明 |
|---|---|---|
| `scene_id` | UUID FK → scenes ON DELETE CASCADE | |
| `agent_id` | UUID FK → agents ON DELETE CASCADE | |
| `enabled` | BOOLEAN NOT NULL DEFAULT true | 场景内启停，不影响其他场景 |
| `sort_order` | INTEGER NOT NULL DEFAULT 0 | 展示/兜底派生顺序 |

主键 `(scene_id, agent_id)`；索引 `(scene_id, enabled, sort_order)`。

#### `integrations` / `integration_keys` / `integration_scenes`

服务端 Key 归属接入方（调用方维度），场景只作为被授权对象：

| 表 | 关键列 | 说明 |
|---|---|---|
| `integrations` | `id`、`name UNIQUE`、`public_code UNIQUE`、`description`、`status`、`metadata` | `public_code` 用于匿名嵌入归属；`status=disabled` 时名下全部 Key 与公开标识立即失效 |
| `integration_keys` | `id`、`integration_id FK CASCADE`、`key_hash UNIQUE`、`hint`、`created_at` | 只存 sha256；`hint` 是明文尾号，供后台区分多把 Key |
| `integration_scenes` | `integration_id + scene_id` 联合主键，双向 FK CASCADE | 场景授权关系；未授权场景用该 Key 调用返回 403 |

一个接入方可持多把 Key（轮换过渡期新旧并存），也可被授权到多个场景。删除接入方前需确认无访客会话，否则应改为停用。

`public_code` 是给匿名嵌入用的公开标识（`ic_xxx`），会出现在宿主页源码里，**不是密钥**；嵌入页通过 `data-integration` 传给组件，访客签到时写入 `integration_id`。服务端调用仍必须用 `X-API-Key`，公开标识只解决归属与命名空间，不解决强鉴权。

#### `visitors`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | 服务端签发，不信任客户端任意值 |
| `scene_id` | UUID FK NOT NULL | 访客按场景隔离 |
| `integration_id` | UUID FK NULL | NULL=未带公开标识的浏览器匿名；非空=某个接入方（API Key 或公开标识）下的终端主体 |
| `external_key` | VARCHAR(128) NULL | 宿主页面可传入的稳定标识（可选） |
| `metadata` | JSONB NOT NULL DEFAULT '{}' | |
| `last_seen_at` | TIMESTAMPTZ | |

约束（PG 部分唯一索引，避开 NULL 让 `ON CONFLICT` 失效）：

- `uq_visitors_anon_scene_external (scene_id, external_key) WHERE integration_id IS NULL`
- `uq_visitors_integration_scene_external (integration_id, scene_id, external_key) WHERE integration_id IS NOT NULL`

同一 `external_key` 在不同接入方下互不影响；匿名访客仍按场景复用。`external_key` 可空，空表示不做宿主侧归并，同一命名空间允许多个 NULL。

#### `chat_events`（全量对话埋点）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `trace_id` | VARCHAR(64) | 与响应头 `X-Trace-Id` 对齐 |
| `scene_code` | VARCHAR(32) | 冗余字符串，场景归档后仍可统计 |
| `session_id` / `visitor_id` / `user_id` / `integration_id` | UUID NULL | 客户端传入非 UUID 时落 NULL，避免整条埋点失败 |
| `tenant_id` | UUID NULL | 预留：多租户时由凭证注入，客户端 `ext` 不参与 |
| `ext` | JSONB NOT NULL DEFAULT '{}' | 客户端自定义埋点（扁平标量，≤10 键 / ≤1KB）；渠道（platform）等设备信息都是其中的普通键，不单独建列 |
| `message_chars` | INT NOT NULL DEFAULT 0 | 只记长度，不存消息正文 |
| `total_tokens` | INT NULL | 预留：LLM 用量回填 |
| `created_at` | TIMESTAMPTZ | 索引 `(scene_code, created_at)`、`(visitor_id, created_at)`、`(integration_id, created_at)`、`(trace_id)` |

与 `request_traces` 分工：后者按采样落库、偏排障；`chat_events` 全量、偏统计，不存消息正文与 IP。
写入时机在鉴权通过后、限流之前：被限流的请求也会留痕；写入失败只告警，不影响对话。

**身份回填**：请求级写入时首轮 `session_id` 为空，匿名请求带的 `visitor_id` 是宿主侧 `v_xxx`（不是 UUID），落库会被兜底为 NULL。进入 service 拿到真实 `sessions.id` / `visitors.id` 后，按 `trace_id` 回填一次（`ChatEventRepo.attach_identity`），因此首轮对话也能按会话/访客 join；回填失败只告警。

#### `schema_meta`

| 列 | 类型 | 说明 |
|---|---|---|
| `key` | VARCHAR(64) PK | 例如 `version` |
| `value` | TEXT NOT NULL | 当前 schema 版本 |

启动时断言版本，不匹配直接提示“请全量重建”。

### 5.2 改造现有表

| 表 | 改动 | 最终约束 |
|---|---|---|
| `agents` | 加 `scene_id UUID NULL` FK → scenes | `UNIQUE NULLS NOT DISTINCT (scene_id, name)`；`code` 类型必须 `scene_id IS NULL` |
| `sessions` | 加 `scene_id NOT NULL`、`visitor_id UUID NULL`；`user_id` 改可空 | `CHECK ((user_id IS NULL) <> (visitor_id IS NULL))`；索引 `(scene_id, created_at)` |
| `memories` | 加 `visitor_id UUID NULL`；`user_id` 改可空 | `CHECK ((user_id IS NULL) <> (visitor_id IS NULL))`；`UNIQUE NULLS NOT DISTINCT (user_id, visitor_id, memory_uid)` |
| `knowledge_bases` | 加 `scene_id UUID NULL` | NULL=平台共享；索引 `(scene_id, is_active)` |
| `canned_scripts` | 加 `scene_id UUID NULL` | `UNIQUE NULLS NOT DISTINCT (scene_id, key)`；索引 `(scene_id, enabled, priority)` |
| `tools` | 加 `scene_id UUID NULL`（P1） | 名称保持全局唯一；场景只能绑本场景或全局工具 |
| `request_traces` | 加 `scene_code`、`visitor_id`、`router_skipped`、`router_fallback`、`router_ms`、`prompt_hash` | 索引 `(scene_code, created_at DESC)` |

### 5.3 建表顺序与外键

```text
scenes（不含 fallback 外键）
  → agents（scene_id → scenes）
  → scene_agents（scene_id → scenes, agent_id → agents）
  → visitors（scene_id → scenes）
  → sessions（scene_id → scenes, user_id/visitor_id）
  → memories（user_id/visitor_id）
  → knowledge_bases / canned_scripts / tools（scene_id → scenes）
  → request_traces（scene_code 冗余，不建外键）
  → chat_events（全量埋点，不建外键）
```

注意：

- `scenes.fallback_agent_id` 不建外键，否则与 `agents.scene_id` 形成循环；
- `request_traces.scene_code` 用冗余字符串而不是外键，场景归档后链路仍可查；
- `init_db.py` 按分号切分，不使用 `DO $$` 块、函数体或存储过程。

### 5.4 默认种子

- 默认场景：固定 UUID `00000000-0000-0000-0000-000000000001`，`code=default`，`is_default=true`，`router_mode=direct`；
- 默认 Agent：`default_assistant`，`source=code`，`scene_id=NULL`；
- 绑定：`scene_agents` 通过 `INSERT ... SELECT id FROM agents WHERE name='default_assistant'`；
- 默认知识库：沿用 `cs_default`（是否改名见风险）；
- 不使用演示工单/演示账号作为默认数据。

---

## 6. 运行时流程

### 6.1 入口

```text
POST /api/v1/chat/stream
X-Integration-Code: ic_xxx      // 可选：匿名嵌入的接入方归属（单租户降级后可省略）
{
  "scene_code": "qqq111111",     // 可缺省；缺省=default
  "session_id": null,
  "visitor_id": null,            // 匿名访客；首次由服务端签发
  "message": "...",
  "ext": {"platform": "web"}    // 可选：扁平埋点上下文，不参与身份/隔离/限流
}
```

解析顺序：

1. 有 `Authorization` → 客户身份，`actor_key=user:<uuid>`；
2. 无 Authorization 且有 `X-API-Key` → 服务端接入方，校验场景授权后按接入方命名空间落访客；
3. 无 Authorization、无 API Key，但有 `X-Integration-Code` 且接入方已授权 → 匿名访客归属该接入方，`actor_key=visitor:<uuid>`；无效标识 401、未授权场景 403、场景未开放匿名 401；
4. 以上都没有且场景已发布（`publish_key` 非空）→ 场景级匿名访客；
5. 都不满足 → 401；
6. `scene_code` 缺省 → 默认场景；未知/停用 → 3003/3004。

### 6.2 编排

```text
resolve_scene
  → get_or_create_session(scene, actor, session_id)
  → 固定话术匹配（全局 probing + 本场景 identity/capability）
  → build_scene_router_profile(scene)
      ├─ 0 个 Agent：抛 SceneNotConfigured（3005）
      ├─ 1 个 Agent / mode=direct：直连，router_skipped=true
      └─ ≥2 个 Agent：RouterAgent.route(profile)
  → 逐个 intent 解析业务 Agent（限定本场景池）
  → 业务 Agent 构建工具（RAG 限定本场景，Memory 用 actor_key）
  → 落库 + 写 trace（scene_code、prompt_hash、router_*）
  → done
```

### 6.3 直连语义

- 置信度固定 1.0，`reason="direct"`；
- 仍然发一条 `route` 事件，带 `router_skipped=true`，前端无需区分；
- 阈值、最大意图数、兜底话术对直连不生效；
- 唯一 Agent 被停用/解绑后，场景变为“未配置”，不是“直连到空”。

### 6.4 路由档案

`build_scene_router_profile(scene)` 输出：

| 字段 | 说明 |
|---|---|
| `scene_code/scene_name` | 展示身份 |
| `mode` | 实际生效模式 |
| `model` | 场景覆盖或全局默认，附来源 |
| `confidence_threshold` | 同上 |
| `max_intents` | 同上 |
| `fallback_agent` | 配置值或池内派生值，附派生原因 |
| `fallback_text` | 场景兜底话术或全局默认 |
| `candidates` | 本场景启用 Agent + 线索完整度 + 截断信息 |
| `warnings` | 0 Agent、仅描述、兜底不在池中、无可检索知识库等 |
| `prompt` | 实时渲染结果 |
| `prompt_hash` | `sha256(prompt)[:12]`，写入路由 span |

---

## 7. API 契约

### 7.1 聊天

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/chat/stream` | SSE 流式；支持 `scene_code` / `visitor_id` |
| POST | `/api/v1/chat/message` | 非流式备用 |
| GET | `/api/v1/chat/history` | 按 session 取历史；校验 scene+actor |

SSE 事件保持现有 `text/status/route/done/error`，新增字段只增不改：

```json
{"type":"route","data":{"agent_name":"...","confidence":1.0,"router_skipped":true,"scene_code":"default"}}
```

### 7.2 公开场景配置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/public/scenes/{code}` | 返回组件展示配置、是否允许匿名、鉴权方式；不返回 prompt/Agent 清单 |
| POST | `/api/v1/public/scenes/{code}/visitors` | 签发 `visitor_id`（按场景隔离） |

### 7.3 后台场景

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/scenes` | 列表；含状态、Agent 数、模式、警告摘要 |
| POST | `/api/v1/admin/scenes` | 创建；code 不可重复 |
| GET | `/api/v1/admin/scenes/{code}` | 详情 |
| PUT | `/api/v1/admin/scenes/{code}` | 编辑；默认场景只读 |
| PATCH | `/api/v1/admin/scenes/{code}/status` | 启用/停用/归档；默认场景不可停用 |
| PUT | `/api/v1/admin/scenes/{code}/agents` | 绑定 Agent 列表（含场景内启停） |
| PUT | `/api/v1/admin/scenes/{code}/widget` | 嵌入配置、允许来源、发布标识 |

### 7.3.1 后台接入方（服务端 Key）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/integrations` | 列表（附 `key_count` 与已授权场景） |
| POST | `/api/v1/admin/integrations` | 新建；名称唯一，`scene_ids` 需为已存在场景 |
| GET | `/api/v1/admin/integrations/{id}` | 详情（含 keys 尾号与场景） |
| PUT | `/api/v1/admin/integrations/{id}` | 更新；不传 `scene_ids` 保持原授权 |
| DELETE | `/api/v1/admin/integrations/{id}` | 删除；已有访客会话返回 3002，请改为停用 |
| POST | `/api/v1/admin/integrations/{id}/keys` | 签发 Key，明文仅本次响应 |
| DELETE | `/api/v1/admin/integrations/{id}/keys/{key_id}` | 吊销 Key，立即失效 |

> 单租户降级（U20）：后台隐藏接入方菜单与场景接入抽屉的归属选择，嵌入代码不再带 `data-integration`；接口全部保留。种子只创建一个默认接入方（`ic_default`）并授权全部场景，新建场景也会自动授权给所有启用接入方，已有 Key 不会因新场景 403。

### 7.4 后台路由查看

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/router/template` | 全局模板、占位符、常量、env 默认值 |
| GET | `/api/v1/admin/router/scenes` | 场景路由摘要列表，不渲染 prompt |
| GET | `/api/v1/admin/router/scenes/{code}` | 生效配置 + 候选 + 警告 |
| GET | `/api/v1/admin/router/scenes/{code}/prompt` | 提示词正文 |
| POST | `/api/v1/admin/router/scenes/{code}/test` | 试运行；会消耗一次模型调用 |
| GET | `/api/v1/admin/router/scenes/{code}/stats` | 24h 路由量、兜底率、转人工率、平均耗时 |

兼容：保留 `GET /admin/router` 与 `/admin/router/prompt` 作为默认场景别名，至少保留一个版本。

### 7.5 错误码

| 码 | HTTP | 含义 |
|---|---|---|
| 3003 | 404 | 场景不存在 |
| 3004 | 404 | 场景已停用/归档 |
| 3005 | 409 | 场景未配置可用 Agent |
| 4029 | 429 | 请求过于频繁（沿用） |

---

## 8. 后台页面

| 页面 | 改造 |
|---|---|
| 场景管理（新增） | 列表、创建、编辑、停用、绑定 Agent、嵌入配置、轮换 key、复制嵌入代码 |
| 路由信息（重写） | 左侧场景列表 + 右侧详情；`直连/LLM/未配置/已停用` 徽标；生效值 + `继承/覆盖` 标签；候选表；警告；试运行；跳转链路 |
| Agent 列表 | 增加“所属场景”列与筛选；详情带 `?scene=` 或按 id 查 |
| 知识库 | 全局资源不做场景归属；“未被使用”警告；新建时建议 `s_{scene_code}_` 前缀区分业务 |
| 固定话术 | 增加场景筛选；`probing` 标为全局；场景内可覆盖同 key |
| 链路 | 增加场景列/筛选；按 Agent 名筛选必须同时带场景 |
| MCP | 绑定反查显示 `scene/agent`；场景归属（P1） |

前端不要做“每个场景一个 tab”，场景数量增长后撑不住；用可搜索列表 + 详情页。

---

## 9. 开发里程碑

### M0 契约与配置（S）

- [x] `schemas/scene.py`：`SceneStatus`、`RouterMode`、`ActorType`、`SceneWarningCode`、默认场景常量。
- [x] `core/exceptions.py`：`SceneNotFound(3003)`、`SceneDisabled(3004)`、`SceneNotConfigured(3005)`。
- [x] `core/config.py`：公开聊天限流、Origin 缓存 TTL；默认知识库沿用现有配置。
- [x] `ChatRequest` 增加 `scene_code/visitor_id/client_msg_id`。
- [x] 前后端 types 对齐。

验收：枚举、字段名、错误码在三端一致。

### M1 数据库与初始化（L）

- [x] `models/entities.py`：新增 `Scene/SceneAgent/Visitor/SchemaMeta`；改造 7 张表。
- [x] 重写 `scripts/init_db.sql`：最终 schema、约束、索引、知识库种子；删除历史增量迁移段。
- [x] `scripts/verify_schema.py`：结构断言 + 约束行为断言 + 种子断言 + 幂等。
- [x] `scripts/seed_data.py`：移除 `Base.metadata.create_all`；种子按场景组织。
- [x] `backend/app/setup.py`：不再独立 `create_all`，复用初始化 SQL/函数。
- [x] `scripts/rebuild_and_seed.py`：`DROP_TABLES` 增加新表；种子顺序调整；结尾跑校验。
- [x] `schema_meta` 版本守卫接入启动流程。
- [x] 清理说明：Qdrant、`memory_data/memory.db`、`data/knowledge_bases`、`data/doc_archives`。

验收：空库一次初始化 + `verify_schema.py` 全绿；脚本重复执行行数不变。

### M2 场景、默认场景、会话（L）

- [x] `agents/business/default_agent.py`：中立提示词、默认知识库、memory+rag。
- [x] 内核 Agent 注册与重名保护。
- [x] `SceneRepo/VisitorRepo`；`AgentRepo/SessionRepo` 场景化。
- [x] `bootstrap.ensure_default_scene()` 幂等兜底；默认场景只读保护。
- [x] `ChatService` 接收 scene+actor；会话归属校验。
- [x] `/chat/stream` 支持 `scene_code`；无码/错码/停用语义。
- [x] `admin_scenes.py` + 后台场景管理页。
- [x] 测试：无码直连、错码 404、停用 404、默认 Agent 不可停用。

验收：现有客户前端显式传 `cs` 后行为不变；无码只命中默认知识库。

### M3 场景路由档案与后台查看（L）

- [x] `agents/scene_router.py`：`SceneRouterProfile`、警告、prompt_hash。
- [x] `RouterAgent.route(profile)`；提示词增加 `{scene_rules}`。
- [x] `Orchestrator.dispatch(scene, profile)`；直连分支与 trace。
- [x] 路由 span 记录 `scene_code/candidate_count/prompt_hash`。
- [x] 后台路由 6 个接口 + 兼容别名。
- [x] 前端路由页重写（主从结构、试运行、警告、继承/覆盖）。
- [x] Agent 查看补场景维度。
- [x] 测试：0/1/N Agent、兜底降级、prompt_hash 稳定性。

验收：页面与运行时同源；每个场景能解释自己的路由差异。

### M4 隔离补齐（L）

- [x] 固定话术场景化：场景优先合并、后台修改即时生效（无缓存）、同 key 场景行覆盖全局。
- [x] 知识库范围：`search_all` 限定本场景；平台共享库需显式写进 `rag_namespace(s)`；场景绑定接口拒绝抢绑。
- [x] 记忆 actor 隔离：`user:` / `visitor:` actor_key，镜像行按 user/visitor 二选一归属。
- [x] 会话/历史/清空按 scene+actor 隔离；跨场景访问按不存在处理（不泄露存在性）。
- [x] 链路场景筛选；列表/详情/筛选均带 `scene_code` 与路由标记。
- [ ] MCP/工具场景归属（P1，延后：工具仍通过绑定的 Agent 反查场景）。
- [x] 测试：`tests/test_scene_isolation.py` 覆盖话术、知识库、记忆、会话、链路五类跨场景隔离。

验收：A 场景无法通过任何路径读到 B 场景的数据。

落地备注：
- 话术合并顺序为「同 key 场景行替换全局行 → 再按 enabled 过滤」，场景行被停用时不会静默回落到全局文案。
- 知识库已改为全局资源（不再记 `knowledge_bases.scene_id` 归属）：同库可被不同场景的 Agent 绑定，跨业务区分交给 namespace 命名与配置纪律；`search_all_knowledge_bases` 运行时覆盖全部启用库（后台默认不开启，Agent 配置弹窗已不展示该字段）。
- 咨询列表/导出、链路列表仍带 `scene_code`；场景弹窗只保留 Agent 绑定（知识库归属接口已移除）。

### M5 对外接入（M）

- [x] 公开场景配置接口；访客签发。
- [x] 聊天鉴权：Authorization / 接入方 API Key / 场景发布标识 / 已发布匿名；Key 按接入方签发与吊销。
- [x] 动态 CORS：按场景来源校验，处理 preflight，缓存 + `Vary: Origin`。
- [x] Redis 限流：scene/IP/visitor + 并发流上限；超限 4029。
- [ ] 幂等 `client_msg_id`（P1，暂缓：字段已预留，尚未做去重存储）。
- [x] `docs/api.md` 增补公开接口、错误码、curl 示例。

验收：外域页面可接入；无 key 被拒；突发请求 429。

落地备注：
- 鉴权优先级：登录客户（Bearer）> `X-API-Key`（按 `key_hash` 反查接入方，再校验场景授权；Key 无效 401、未授权 403）> `X-Publish-Key`（公开标识，传错 401）> 已发布场景匿名 > 401；`publish_key` 为 NULL 表示未开放匿名，签发访客同样 403。
- Key 归属接入方（`integrations`）而不是场景：一个接入方可持多把 Key 并被授权到多个场景，后台「接入方管理」签发/吊销；场景详情不再有 `api_key_set`。
- 访客按 `integration_id` 分命名空间：同一 `external_key` 在不同接入方下互不影响；不带 Key 的匿名访客 `integration_id` 为空。
- 动态 CORS 同时放行全局 `ALLOWED_ORIGINS` 与场景 `allowed_origins`；聊天 body 不参与场景识别，嵌入页需在 URL 带 `?scene_code=` 或请求头带 `X-Scene-Code`。
- 限流键按 `scene + user/visitor/ip` 分桶，分钟/日/并发三档默认 30/2000/3；Redis 故障 fail-open，SSE 在 finally 释放并发槽位。
- 访客签发的 `visitor_id` 即 `visitors.external_key`（`v_` 前缀），聊天原样传入 `visitor_id` 即可复用；不信任客户端自带的行 ID。
- M6 嵌入组件接入时直接复用 `GET /public/scenes/{code}` + `POST .../visitors`，按 `widget` 配置渲染主题与欢迎语。

### M6 嵌入组件（M）

- [x] `embed.html` + `src/embed/main.ts` 独立 Vite entry，不引入 Element Plus（构建产物约 5KB）。
- [x] `public/widget.js`：一行 script，创建气泡 + iframe；移动端自动全屏。
- [x] postMessage 通知（`cs:widget:ready/done/close/error`）驱动关闭与未读角标；主题、访客标识由 iframe 内公开配置自主管理，不经宿主传递。
- [x] 后台接入页：嵌入配置、允许来源、匿名开关、嵌入代码（悬浮气泡 / 直嵌两种）、模拟宿主页的悬浮气泡实时预览；服务端 Key 统一在「接入方管理」签发与吊销。
- [x] 移动端全屏、样式隔离（iframe + 独立入口，宿主 CSS 不进入组件）。
- [x] 第三方静态宿主页 E2E（`/embed-demo.html`，气泡/欢迎语/流式回复/关闭均实测通过）。

落地备注：

- 嵌入对话固定带 `?scene_code=` 与 `X-Scene-Code`，与动态 CORS 的场景识别约定一致；请求 body 里的 `scene_code` 不参与 CORS 判定。
- 访客标识存 `localStorage: cs_embed_visitor_{scene}`，同一浏览器复用；未开放匿名且 URL 无 `?token=` 时，组件内直接提示无法开始对话。
- 气泡只服务匿名访客：`visitor_id` 无鉴权，不用于承载宿主站客户 id；需要登录客户身份时，由宿主站自研前端（customer token）或宿主后端 BFF（`X-API-Key`）直接对接聊天接口。
- 气泡标题/主题色/位置来自 `GET /public/scenes/{code}` 的 `widget`，保存后刷新宿主页生效；`data-*` 属性只作为配置读取失败时的兜底。
- 后台 `PUT /admin/scenes/{id}/widget` 对 `widget_config` 是整份替换；来源归一化失败返回 2002；`allow_anonymous` 由 false→true 生成 `pk_`，true→false 清空。
- 本地联调：两个前端 vite 均支持 `VITE_API_TARGET`（默认 8000）；嵌入地址可用 `VITE_EMBED_URL` 覆盖，未配置时复用 `VITE_PUBLIC_CHAT_URL`。

验收：一行 script 可用；宿主 CSS 不影响组件；组件不污染宿主全局（仅暴露 `window.AgentDockWidget`）。

### M7 验证与收尾（M）

- [x] 既有测试适配（路由、编排、追踪、动态 Agent、话术、知识库、链路）。
- [x] `pytest`、`ruff`、两端构建、`compileall`。
- [x] 空库端到端：重建 → 校验 → 默认直连 → 创建麦当劳场景（2 Agent + KB + MCP）→ 试运行 → 嵌入对话。
- [x] 文档更新：API、配置、部署、运行手册、代码资产、README、CHANGELOG。
- [x] 旧路由接口兼容别名保留一个版本，标注移除计划（计划 v0.3 移除）。

落地备注：

- 回归基线：`ruff` 全绿，`compileall` 通过，`pytest` 416 passed / 3 skipped；`frontend` 与 `admin-frontend` 均构建通过（embed chunk 约 5KB）。
- 空库重建分两种模式各跑一次：`SEED_DEMO=false` 得到 staff=1 / agents=7 / tools=2 / 零业务数据（验证生产初始路径）；`SEED_DEMO=true` 得到 staff=2 / customer=1 / agents=8 / tools=3 / documents=6 / tickets=4 / scenes=2（默认场景 + 演示客服场景 `customer_service`），行数与种子声明一致。
- 默认场景：客户登录后无 `scene_code`，`routed_to=default_assistant`，直连不路由。
- 麦当劳场景：后台建 2 个动态 Agent（订单助手绑 `mcp_logistics`/`mcp_refund`，菜单顾问）→ 建场景 `mcd_order`（auto）→ 绑定顺序 → 归属 `cs_order`/`cs_after_sales`（其他库变为不可抢绑）→ 开放匿名。
- 匿名对话「查订单 SO20240001」路由到 `mcd_order_agent` 并调用物流 MCP，返回真实模拟轨迹；路由试运行「汉堡优惠」选中 `mcd_menu_agent`（置信度 0.95，prompt_hash `9695147751a6`）。
- 嵌入页在浏览器实测：公开配置/访客签发/SSE 均 200，主题色/标题/欢迎语与后台一致，控制台无错误；宿主页仅 `window.AgentDockWidget` 一个全局。

验收：全链路回归通过，文档与实现一致。

---

## 10. 初始化与验证

### 10.1 唯一 DDL 源

- `scripts/init_db.sql`：唯一建表/约束/索引/静态种子源；
- `scripts/init_db.py`：执行 SQL（按分号切分，忽略 42P07/42710/42P06）；
- `scripts/seed_data.py`：只写动态种子，不建表；
- `backend/app/setup.py`：只建管理员/默认场景，不建表；
- `scripts/rebuild_and_seed.py`：DROP → init SQL → 种子 → 校验。

### 10.2 重建步骤

```bash
# 1. 停服务
# 2. 清理非 SQL 状态（按需）
#    - Qdrant collection / namespace
#    - backend/memory_data/memory.db
#    - backend/data/knowledge_bases/
#    - backend/data/doc_archives/
# 3. 重建
python scripts/rebuild_and_seed.py
# 4. 校验
python scripts/verify_schema.py
# 5. 启动
```

### 10.3 verify_schema.py 断言

**结构**

- 新表/新列/索引/约束存在；
- `sessions.scene_id NOT NULL`；
- `agents` 的唯一约束是 `(scene_id, name)` 且 NULLS NOT DISTINCT；
- `scenes` 的默认场景部分唯一索引存在。

**约束行为**

- 重复 `scene.code` → 失败；
- 两个 `is_default=true` → 失败；
- 同场景同名 Agent → 失败；不同场景同名 → 成功；
- 同场景重复 canned key → 失败；全局 + 场景同 key → 成功；
- `sessions` 同时无 user/visitor 或同时有 → 失败；
- 默认场景删除/停用由 API 层拦截（脚本不测）。

**种子**

- 默认场景恰好一个；
- 默认场景恰好一个绑定 Agent，且 `router_mode=direct`；
- 默认 Agent 存在且 `enabled=true`；
- 默认知识库 `cs_default` 存在；
- 无演示工单/演示账号。

**幂等**

- `init_db.py` 连跑两次不报错；
- `seed_data.py` 连跑两次行数不变。

### 10.4 启动冒烟

- 无码请求 → `scene_code=default`，`router_skipped=true`；
- 错码 → 3003；停用 → 3004；
- 默认场景绑定 Agent 被停用 → 固定维护话术；
- 管理后台路由页能列出默认场景并显示“直连”。

---

## 11. 测试矩阵

| 文件 | 覆盖 |
|---|---|
| `tests/test_schema_contract.py` | 表/列/索引/约束、幂等、默认种子 |
| `tests/test_default_scene.py` | 无码直连、默认 KB、默认 Agent 保护 |
| `tests/test_scene_crud.py` | 创建/绑定/停用/归档、code 不可改、默认场景只读 |
| `tests/test_scene_router_profile.py` | 0/1/N Agent、继承/覆盖、兜底降级、警告、prompt_hash |
| `tests/test_scene_isolation.py` | 话术、知识库、记忆、会话、链路隔离 |
| `tests/test_public_chat_auth.py` | 发布标识、API key、Origin、429 |
| `tests/test_embed_widget.py` | 配置接口、visitor 签发、主题字段 |
| 既有测试 | 适配 scene 参数与默认场景行为 |

---

## 12. 风险与边界

| 风险 | 处理 |
|---|---|
| `scenes.fallback_agent_id` 与 `agents.scene_id` 循环外键 | fallback 不建外键，应用层校验；建表顺序 scenes→agents |
| 同名 Agent 在后台/链路串场 | 所有按名查询必须带 scene；列表显示场景列 |
| 默认知识库改名 | 改 `cs_default` 必须同时清 Qdrant 旧 namespace；本期默认不改名，后续单独评估 |
| Qdrant 旧向量 | SQL 重建清不掉；重建前按 runbook 清理或换 collection |
| `memory_data/memory.db` | 与 DB 记忆双存储；重建时一并清理 |
| CORS | Origin 不是安全边界；真正防线是 API key + 限流 + 注入检测 |
| 场景删除 | 有会话/工单/链路时只归档，不物理删除 |
| 统计成本 | 列表聚合用拍平列，不扫 `spans` JSONB |
| `init_db.py` 切分器 | 禁止 `DO $$`、函数体、存储过程 |
| 旧接口兼容 | 旧 `/admin/router` 保留一个版本，CHANGELOG 标注移除计划 |

---

## 13. 本期不做

- 场景草稿/发布审批流；
- 按场景的后台人员 RBAC；
- 计费、配额、发票；
- 每个场景独立的自定义工具代码上传；
- 历史数据迁移；
- 多语言场景模板。

---

## 14. 变更记录

| 日期 | 内容 |
|---|---|
| 2026-10-21 | 初版：多场景目标、默认场景、路由档案、数据模型、M0–M7 里程碑 |
