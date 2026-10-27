# U18 · 接入方与 Key 归属：从场景级 Key 迁移到 integrations

> 状态：已落地 ｜ 影响面：`scenes` / `visitors` 表结构、聊天鉴权链路、访客命名空间、新增 `integrations` / `integration_keys` / `integration_scenes` 表与后台「接入方管理」页
>
> 关联：[U07](07-staff-customer-auth-split.md)（身份边界拆分思路一致）、[U09](09-async-tool-execution.md)（服务端对接）、多场景文档 M5/M6

## 1. 背景与问题

原方案的 `scenes.api_key_hash` 把服务端 Key 绑在场景上，只够「一个场景一个调用方」的演示形态，真实接入会暴露三个问题：

1. **Key 没有归属**：同一个场景被商城、小程序、BFF 三方调用时只能共用一把 Key，无法单独轮换或吊销某一方；
2. **轮换影响面过大**：换发场景 Key 会让所有调用方同时 401，只能停机窗口操作；
3. **访客串号**：`visitors` 只按 `(scene_id, external_key)` 唯一，不同调用方传入相同客户标识（如 `user_1001`）会命中同一访客，会话与记忆互相污染。

## 2. 设计：Key 归接入方，场景只作为被授权对象

```
integrations          接入方（服务端调用方）：name UNIQUE / status(active|disabled) / metadata
integration_keys      Key：integration_id FK CASCADE / key_hash UNIQUE / hint（明文尾号6位）
integration_scenes    授权关系：(integration_id, scene_id) 联合主键，双向 FK CASCADE
```

- 一个接入方可持多把 Key（轮换过渡期新旧并存），也可被授权到多个场景；
- 库里只存 `sha256` 十六进制，明文 `sk_...` 只在签发响应出现一次；`hint` 仅尾号，供后台区分多把 Key；
- `status=disabled` 时 `resolve_key` 直接过滤掉该接入方，名下全部 Key 立即失效，重新启用即可恢复。

## 3. 鉴权链路（`chat.py::_resolve_and_authorize`）

顺序不变，但 `X-API-Key` 的语义从「按场景比对哈希」改为「反查接入方 + 校验场景授权」：

1. 登录客户（Bearer）优先：带了 customer token 就不再解析 API Key；
2. `X-API-Key` 反查启用中的接入方：Key 不存在/接入方停用 → 401（**不降级为匿名**，避免以为在隔离却串到匿名池）；
3. Key 有效但目标场景不在授权列表 → 403；
4. `X-Publish-Key` / 已发布场景匿名 / 401 的原有顺序保持不变。

`integration_id` 随 `ChatService.stream/reply` 透传到 `_resolve_actor`，登录客户仍走 `user` 分支，不受影响。

## 4. 访客命名空间：两个部分唯一索引

`visitors` 增加 `integration_id`（NULL=浏览器匿名）。PostgreSQL 中 NULL 互不相等，单条 `UNIQUE` 会让
`ON CONFLICT` 在匿名场景失效，因此按是否为空拆成两个部分唯一索引：

```sql
uq_visitors_anon_scene_external        (scene_id, external_key)                 WHERE integration_id IS NULL
uq_visitors_integration_scene_external (integration_id, scene_id, external_key) WHERE integration_id IS NOT NULL
```

`VisitorRepo.get_or_create` 据此分两个 `ON CONFLICT` 分支（`index_where`），保证：

- 匿名访客仍按场景复用（嵌入气泡行为不变）；
- 同一 `external_key` 在不同接入方下是两行，会话/记忆互不干扰；
- 同一接入方重复访问复用同一行。

## 5. 后台与接口

新增 `admin-frontend` 「接入方管理」页（列表、创建/编辑、场景多选授权、Key 签发/吊销，明文仅展示一次；
场景页的「服务端 Key」区块改为跳转提示）。接口：

```
GET/POST         /api/v1/admin/integrations
GET/PUT/DELETE   /api/v1/admin/integrations/{id}
POST             /api/v1/admin/integrations/{id}/keys         # 明文仅本次响应
DELETE           /api/v1/admin/integrations/{id}/keys/{key_id}
```

- 更新时**不传 `scene_ids` 保持原授权**，传空数组才清空；
- 已有访客会话的接入方禁止删除（409/3002），提示改为停用，避免级联清空会话；
- 场景详情不再回显 `api_key_set`，`POST /admin/scenes/{id}/rotate-key` 已移除，不留双轨。

审计事件：`integration.create/update/delete` 与 `integration.key.create/revoke`。

## 6. 数据迁移与兼容

本项目不做增量迁移：`SCHEMA_VERSION` 由 `2` 升到 `3`，旧库需执行 `scripts/rebuild_and_seed.py` 全量重建。

- `scenes.api_key_hash` 删除；
- 旧的场景级 Key 无法自动映射到接入方（原始调用方信息从未记录），重建后需在后台重新签发；
- `scripts/verify_schema.py` 已覆盖新表、约束、部分索引与命名空间行为。

## 7. 验证

- `pytest`：398 passed（新增接入方后台 API、鉴权与命名空间用例）；
- `ruff check app tests`：通过；`vue-tsc --noEmit`：通过；
- `scripts/verify_schema.py`：25 项通过（含「不同接入方相同 `external_key` 互不影响」「同接入方重复被拒」）；
- 本地端到端：创建两个接入方 → 同 `external_key` 各落一行访客 → 无效 Key 401 → 未授权场景 403 → 有访客时删除接入方 409。
