# U20 单租户降级与自定义埋点（ext / chat_events）

## 背景

U18/U19 的接入方能力解决的是“不同公司客户 id 相同导致数据串”：Key 归属接入方、场景授权、
访客按 `integration_id` 分命名空间。但当前产品按**独立部署**交付——一个部署只服务一家公司，
同一个客户 id 就是同一个人，接入方带来的多租户隔离在现阶段没有真实收益，反而增加配置负担：

- 嵌入方要先在后台建接入方、授权场景，再复制公开标识填进 `data-integration`；
- 后台菜单多一层“接入方管理”，场景接入抽屉还要选择归属；
- 真正需要区分的是**平台/渠道**（Web、H5、小程序、App、API），而不是“哪个公司”。

因此本迭代做两件事：

1. **单租户降级**：保留接入方表与 Key 能力，运行时只启用一个默认接入方并授权全部场景；
   后台隐藏接入方入口与场景接入抽屉的归属选择，嵌入代码不再带 `data-integration`。
2. **自定义埋点**：聊天入参增加 `ext`（扁平 JSON），把渠道等客户端上下文原样落到独立的
   `chat_events` 表，为后续统计、限流分层与租户模式留数据基础。

## 设计

### 单租户降级（方案 C：逻辑降级，不删表）

| 项 | 处理 |
|---|---|
| 数据表 | `integrations` / `integration_keys` / `integration_scenes` 原样保留 |
| 默认接入方 | 种子只创建一个「默认接入方」（`ic_default`），授权所有场景 |
| 新场景 | 后台创建场景后自动授权全部启用中的接入方，已有 Key 不会因新场景 403 |
| 服务端 Key | 能力不变：`X-API-Key` 仍按接入方校验场景授权；Key 在 `/integrations` 直达页签发 |
| 匿名嵌入 | 生成代码与预览不再带 `data-integration`，访客落场景级匿名池（`integration_id IS NULL`） |
| 后台入口 | 隐藏「接入方管理」菜单与场景接入抽屉的「归属接入方 / 服务端 Key」，路由 `/integrations` 保留可直达 |
| 恢复方式 | `ScenesView.vue` 的 `SHOW_INTEGRATION_UI` 改回 `true`、恢复菜单项即可，后端未改语义 |

选逻辑降级而不是物理删除：未来做多租户时，租户可以直接锚定到 `integrations` 记录的
`tenant_id`（已预留在 `chat_events`），不需要重新引回一整套 Key/授权模型。

### 自定义埋点 `ChatRequest.ext`

- 扁平 JSON：值只能是字符串 / 整数 / 布尔，不支持嵌套；
- 最多 10 个字段，字段名 `^[a-z][a-z0-9_]{0,31}$`；
- 字符串值 ≤128 字符，序列化后整体 ≤1KB；
- 敏感键黑名单：`token` / `password` / `authorization` / `secret` / `api_key` / `access_token` / `refresh_token`；
- 越界直接 422（pydantic 校验），不做静默截断。

渠道等设备信息**不单独建列也不单独传参**，直接作为 `ext` 的普通键（如 `{"platform":"web"}`）。
`ext` 只是客户端声明，可伪造，不参与身份、场景隔离与限流分桶；`ext` 任意值都不会进入 Redis key。

### `chat_events`

| 列 | 说明 |
|---|---|
| `trace_id` | 与响应头 `X-Trace-Id` 对齐，便于 grep |
| `scene_code` | 冗余字符串，场景归档后仍可统计 |
| `session_id` / `visitor_id` / `user_id` / `integration_id` | 请求级写入时只填已有值；进入 service 后按 `trace_id` 回填真实会话/访客，客户端非 UUID 值落 NULL |
| `tenant_id` | 预留：多租户时由凭证注入，客户端 `ext` 不参与 |
| `ext` | 原始自定义上下文（渠道等均为其中的键，无独立列） |
| `message_chars` | 只记长度，不存正文 |
| `total_tokens` | 预留：LLM 用量回填 |
| `created_at` | 四个索引：`(scene_code, created_at)`、`(visitor_id, created_at)`、`(integration_id, created_at)`、`(trace_id)` |

写入时机：鉴权通过后、限流之前——被限流的请求也会留痕；旁路写入失败只告警，不影响对话。
与 `request_traces` 的区别：后者按采样落库、偏排障；`chat_events` 全量、偏统计，不存消息正文与 IP。

身份回填：请求级写入时首轮 `session_id` 为空，匿名请求带的 `visitor_id` 是宿主侧 `v_xxx`（不是 UUID），
落库会被兜底为 NULL。进入 service 拿到真实 `sessions.id` / `visitors.id` 后，按 `trace_id` 回填一次，
因此首轮对话也能按会话/访客 join；回填只覆盖非空 UUID，无效值不会把已有列写成 NULL。

## 关键实现

| 位置 | 变更 |
|---|---|
| `backend/app/schemas/api.py` | `ChatRequest.ext` 与校验、EXT 限额常量 |
| `backend/app/models/entities.py` | `ChatEvent` 实体 |
| `backend/app/models/repositories.py` | `ChatEventRepo.record`、`ChatEventRepo.attach_identity`、`IntegrationRepo.grant_scene_to_all` |
| `backend/app/services/chat_service.py` | 拿到真实 session 后按 `trace_id` 回填 `chat_events` 身份（失败只告警） |
| `backend/app/api/v1/chat.py` | 鉴权后、限流前写入埋点，`ext` 原样入库 |
| `backend/app/api/v1/admin_scenes.py` | 新建场景自动授权全部启用接入方（失败只告警） |
| `scripts/init_db.sql` | `chat_events` 表与 4 个索引（含 `trace_id`），`schema_meta=5` |
| `scripts/seed_data.py` / `rebuild_and_seed.py` | 默认接入方 `ic_default` 授权全部场景 |
| `frontend/public/widget.js` / `embed-preview.html` | 透传 `data-ext` |
| `frontend/src/embed/EmbedApp.vue` | 解析 `ext` 并随聊天请求发送 |
| `admin-frontend/src/layouts/AdminLayout.vue` | 隐藏接入方菜单，保留路由 |
| `admin-frontend/src/views/ScenesView.vue` | `SHOW_INTEGRATION_UI=false`：隐藏归属选择，生成代码不带 `data-integration` |

## 验证

- 临时库重建：`chat_events` 与 4 个索引存在，`schema_meta.version=5`，`verify_schema.py` 通过；
- `backend/tests/test_chat_ext.py` 覆盖 ext 合法/非法、埋点透传与失败容错、
  非 UUID 兜底、身份回填（无可用 UUID 不发 UPDATE、按 trace_id 写入真实 id）、建场景自动授权与授权失败不回滚；
- `backend/tests/test_chat_service_tracing.py` 覆盖首轮 session、匿名 visitor 回填与回填失败不阻塞对话；
- 匿名嵌入链路：`widget.js` → iframe → `EmbedApp` → `POST /chat/stream` 的 `ext` 全链路透传；
- 前端 `npm run build`（嵌入 / 后台）通过。

## 已知边界

- `ext` 是客户端声明，可伪造，只能用于统计和非安全策略；强隔离仍靠 Key/租户凭证；
- 当前限流仍按 `scene + user/visitor/ip` 分桶；渠道类信息靠 `ext` 里的约定键（如 `platform`）
  统计，需要时可在 SQL 侧用 `ext->>'platform'` 聚合；任意 `ext` 不进入 Redis key；
- `total_tokens` 暂未回填（`messages` 尚未记录用量），字段先预留；
- 旧库需 `scripts/rebuild_and_seed.py` 全量重建（`SCHEMA_VERSION` 4 → 5）。

## 变更记录

- 评审后去掉了 `chat_events.platform` 独立列与 `platform_from_ext` 归一化：渠道统一放进 `ext`，
  嵌入侧也不再读 `data-platform`。已部署库执行：`DROP INDEX IF EXISTS idx_chat_events_scene_platform_created;`
  `ALTER TABLE chat_events DROP COLUMN IF EXISTS platform;`，并新建 `(scene_code, created_at)` 索引。
