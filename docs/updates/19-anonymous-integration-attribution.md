# U19 匿名接入的接入方归属（integrations.public_code）

## 背景

U18 把服务端 Key 挂到接入方后，API 对接可以确定调用方：`X-API-Key` 反查出 `integration_id`，
访客、会话、记忆都落在对应命名空间。但浏览器匿名嵌入只认场景：

- `publish_key` 是场景级公开标识，同一场景被多个宿主/公司嵌入时共用；
- `POST /public/scenes/{code}/visitors` 签发的访客 `integration_id` 为 `NULL`；
- 后台只能看到“来自哪个场景”，无法按接入方归属，也无法按公司统计 LLM token 与后续定制。

本迭代给接入方增加一个**公开标识**，让匿名嵌入也能确定归属。

## 设计

### 公开标识

- `integrations.public_code`：`VARCHAR(32) NOT NULL UNIQUE`，应用层生成 `ic_` + 16 位十六进制；
- 会出现在宿主页面源码里，定位是**归属标识**，不是密钥；
- 不承担鉴权强度：匿名场景仍受 `publish_key`、`allowed_origins` 与限流约束；
- 停用接入方后，公开标识立即无法解析（与 API Key 一致）。

### 请求传递

| 入口 | 传递方式 |
|---|---|
| 公开场景配置 `GET /public/scenes/{code}` | 请求头 `X-Integration-Code` 或 query `?integration=` |
| 访客签发 `POST /public/scenes/{code}/visitors` | 请求头 `X-Integration-Code` |
| 匿名聊天 `POST /chat/stream` | 请求头 `X-Integration-Code` |
| 嵌入组件 | `<script data-integration="ic_xxx">`，组件自动带到 iframe 与 API 请求 |

鉴权优先级保持“身份强度从高到低”：

1. `Authorization: Bearer <customer token>`（登录客户，忽略其它标识）；
2. `X-API-Key`（服务端 Key，强凭证）；
3. `X-Integration-Code`（匿名归属，公开标识）；
4. `X-Publish-Key` / 已发布场景匿名。

无效公开标识返回 401，不降级为匿名池；接入方未授权该场景返回 403；场景未开放匿名时，
公开标识也不能绕过（401）。这与“Key 无效不降级”的语义一致。

### 数据落点

- 访客签发：`VisitorRepo.get_or_create(scene_id, external_key=v_xxx, integration_id=...)`；
- 聊天：`_resolve_and_authorize` 返回 integration，`ChatService._resolve_actor` 把它传给访客仓储；
- 会话与记忆仍按 `visitor.id` 隔离，因此同一场景下不同接入方的匿名访客天然分开；
- 后续按接入方统计 token：`request_traces.visitor_id` 关联 `visitors.integration_id` 即可聚合；
  登录客户不属于接入方，保持 `integration_id` 为空。

### 兼容与边界

- 不带 `data-integration` 的旧嵌入代码继续工作，访客仍落场景级匿名池（`integration_id IS NULL`）；
- 公开标识可被伪造，和 `publish_key` 一样，不能当作“谁有权调用”的安全边界；
  需要强隔离时仍应使用服务端 `X-API-Key`；
- `SCHEMA_VERSION` 由 3 升到 4，旧库需全量重建。

## 关键实现

| 位置 | 变更 |
|---|---|
| `backend/app/models/entities.py` | `Integration.public_code` |
| `backend/app/models/repositories.py` | `IntegrationRepo.new_public_code` / `to_dict` / `resolve_public_code` |
| `backend/app/api/v1/chat.py` | 匿名聊天解析 `X-Integration-Code`，无效 401 |
| `backend/app/api/v1/public.py` | 公开配置/访客签发解析公开标识，写入访客命名空间 |
| `backend/app/services/scene_service.py` | 公开标识不得绕过场景“开放匿名” |
| `frontend/public/widget.js` | 读取 `data-integration`，带到 iframe 与配置请求 |
| `frontend/src/embed/EmbedApp.vue` | 访客缓存键、公开配置、访客签发、SSE 全部带公开标识 |
| `admin-frontend/src/views/IntegrationsView.vue` | 展示并复制公开标识 |
| `admin-frontend/src/views/ScenesView.vue` | 接入抽屉选择归属接入方，生成带 `data-integration` 的代码与预览 |
| `scripts/init_db.sql` | `public_code` 列、唯一约束、`schema_meta=4` |
| `scripts/seed_data.py` / `rebuild_and_seed.py` | 演示模式创建“演示接入方”并授权客服场景 |

## 验证

- 临时库 `SEED_DEMO=true` 重建：`scenes=2`、`integrations=1`、`integration_scenes=1`；
- `scripts/verify_schema.py` 覆盖 `public_code` 列、唯一约束与重复值拒绝；
- `backend/tests/test_public_access.py` 覆盖公开配置归属、无效/未授权拒绝、访客命名空间、
  匿名聊天公开标识解析；
- 前端 `npm run build` 验证嵌入与后台类型。

## 已知边界

- 公开标识仍是公开信息，不能防住拿到它的第三方；
- 同一公司若拆成多个接入方，同一终端客户会落在不同访客命名空间，会话与记忆不互通；
- 目前限流仍按 `scene + user/visitor/ip` 分桶，尚未按接入方配额；归属数据已具备，
  后续可在此基础上增加按接入方的 token 预算与配额。
