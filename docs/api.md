# API 速查（v2.0）

Base URL：`/api/v1`，响应统一为 `{ "code": 0, "message": "ok", "data": {...} }`（SSE / CSV / 文件下载 / Prometheus 除外）。
Swagger：`http://localhost:8000/docs`

**鉴权角色**（`app/api/deps.py`）：

- `current_customer` —— 客户端客户（`customer_users`）
- `require_staff` —— 后台人员，`agent` 或 `admin` 均可（`staff_users`）
- `require_admin` —— 仅 `admin`

> 人员与客户分表后登录入口分离（[U07](updates/07-staff-customer-auth-split.md)）：后台走 `/auth/staff/login`，客户端走 `/auth/customer/login`（账号不存在返回可识别错误码 `2004` 以引导注册），客户端支持 `/auth/customer/register` 自助注册。Token 内含 `sub`(用户ID)+`role`，`/auth/refresh` 按 `role` 回查对应表。

## Auth

```
POST   /api/v1/auth/staff/login         # 后台人员登录（admin-frontend）→ 双 Token
POST   /api/v1/auth/customer/login      # 客户登录（frontend）；账号不存在→2004，引导注册
POST   /api/v1/auth/customer/register   # 客户自助注册，成功即下发 Token 自动登录
POST   /api/v1/auth/refresh             # 刷新 Token（body: refresh_token）
GET    /api/v1/auth/me                  # 当前用户（任意已登录角色）
```

## Public（无需登录，嵌入 / API 对接）

（[多场景开发文档](multi-scene-development.md) M5）

```
GET    /api/v1/public/scenes/{code}           # 公开场景配置：展示配置/是否允许匿名/鉴权方式；不含 Agent、提示词、知识库
POST   /api/v1/public/scenes/{code}/visitors  # 签发匿名访客（仅已发布场景；返回 visitor_id）
```

- 场景未发布（`publish_key` 为空）时，公开配置仍可读，但 `allow_anonymous=false`，签发访客返回 403；
- `visitor_id` 是给宿主页面存 localStorage 的稳定标识（对应 `visitors.external_key`），聊天时原样传入即可复用同一访客的会话与记忆；
- `publish_key` 是公开标识（会出现在嵌入页源码里），不是密钥；服务端接入 key 只能用 `X-API-Key`，后台轮换后明文只回显一次。

```bash
# 1. 读取公开配置
curl http://localhost:8000/api/v1/public/scenes/mcd_order

# 2. 签发访客（返回 {"visitor_id":"v_xxx","scene_code":"mcd_order"}）
curl -X POST http://localhost:8000/api/v1/public/scenes/mcd_order/visitors

# 3. 匿名嵌入聊天（SSE；scene_code 同时放在 query，供 CORS 中间件识别场景来源）
curl -N -X POST "http://localhost:8000/api/v1/chat/stream?scene_code=mcd_order" \
  -H "Content-Type: application/json" \
  -H "X-Scene-Code: mcd_order" \
  -H "X-Publish-Key: pk_xxx" \
  -d '{"scene_code":"mcd_order","visitor_id":"v_xxx","message":"我的订单到哪了"}'

# 4. 服务端 API key 对接（不需要 scene_code 时落到 default 场景）
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_xxx" \
  -d '{"scene_code":"mcd_order","message":"你好"}'
```

前端嵌入只用一行 script（气泡 + iframe 隔离，主题/标题/欢迎语来自后台「接入」配置）：

```html
<script src="https://your-host/widget.js" data-scene="mcd_order"></script>
```

组件只向宿主暴露 `window.AgentDockWidget.open()` / `close()` / `toggle()`；宿主页可监听 `cs:widget:done` 等 postMessage 事件。开发环境演示页：`http://localhost:5173/embed-demo.html`。

## Chat（核心）

```
POST   /api/v1/chat/stream              # SSE 流式对话
POST   /api/v1/chat/message             # 非流式备用，返回 {data, trace_id}
```

请求体（`ChatRequest`）：

```json
{
  "scene_code": "mcd_order",
  "session_id": null,
  "visitor_id": "v_xxx",
  "message": "我的订单到哪了"
}
```

`scene_code` 缺省 = 默认场景（`default`，单 Agent 直连，不路由）；未知 3003、停用 3004、无可用 Agent 3005，不会静默回落其它场景。

**鉴权顺序**（登录客户 / 服务端 key / 嵌入发布标识 / 已发布匿名，四者取一）：

1. `Authorization: Bearer <customer token>` → 客户身份（`user:<id>`）；带了但无效仍报 401，不降级为匿名；
2. `X-API-Key: sk_xxx` → 按场景 `api_key_hash` 做 sha256 比对；
3. `X-Publish-Key: pk_xxx` → 公开标识，主动传错按 401 处理；
4. 场景已发布（`publish_key` 非空）→ 匿名访客放行（`visitor:<id>`）；
5. 都没有 → 401/2001。

**限流**：Redis 分钟频次 / 日配额 / 并发流三档（默认 30 次/分、2000 次/日、3 条并发），按 `scene + user/visitor/ip` 分桶；超限 4029/429，Redis 故障时放行（fail-open）。SSE 在流结束（含异常/断连）时释放并发槽位。

**跨域**：嵌入页需在请求 URL 带 `?scene_code=` 或请求头带 `X-Scene-Code`（中间件不读 body）；Origin 必须在全局 `ALLOWED_ORIGINS` 或该场景 `allowed_origins` 中，未授权 preflight 返回 403/2003。

> 入口对 `message` 做 `detect_injection`，命中抛 `4003 UnsafeInput`。响应头带 `X-Trace-Id`，与落库链路对齐（[U04](updates/04-request-tracing.md)）。

## Sessions（客户，current_customer，仅本人）

```
GET    /api/v1/sessions                 # 会话列表（分页 page/page_size）
POST   /api/v1/sessions                 # 新建会话（body: title?）
GET    /api/v1/sessions/{id}            # 会话详情
PATCH  /api/v1/sessions/{id}            # 更新标题/状态
DELETE /api/v1/sessions/{id}            # 删除会话
GET    /api/v1/sessions/{id}/messages   # 历史消息（分页）
```

## Memory（current_customer / require_staff）

```
GET    /api/v1/memory/me                # 自己的记忆（可 ?type=）
GET    /api/v1/memory/users/{user_id}   # 某用户记忆（staff）
POST   /api/v1/memory/search            # 语义检索（staff；body: user_id?, query, limit）
PATCH  /api/v1/memory/{id}              # 修正记忆内容/重要性（staff）
DELETE /api/v1/memory/{id}              # 遗忘记忆（staff）
```

## Knowledge（读 require_staff，写 require_admin）

命名空间（[U08](updates/08-knowledge-base-admin.md)）：

```
GET    /api/v1/knowledge/bases                    # 列表（附 document_count）
POST   /api/v1/knowledge/bases                    # 新建命名空间（^[A-Za-z0-9_]+$，重名 3002）
PATCH  /api/v1/knowledge/bases/{namespace}        # 改展示名/描述/启停
DELETE /api/v1/knowledge/bases/{namespace}        # 删除（仍有文档→3002，需先删文档）
```

文档：

```
POST   /api/v1/knowledge/documents                # 上传（multipart: file, namespace?, title?；admin）
GET    /api/v1/knowledge/documents                # 列表（?namespace/status/kind + 分页）
GET    /api/v1/knowledge/documents/{id}/raw       # 下载原文（FileResponse；无归档→3001）
GET    /api/v1/knowledge/documents/{id}/chunks    # 该文档已索引分块（游标 ?offset/limit）
DELETE /api/v1/knowledge/documents/{id}           # 删除（清 Qdrant 向量 + 归档 + DB，admin）
POST   /api/v1/knowledge/documents/{id}/reindex   # 重建索引（admin）
GET    /api/v1/knowledge/chunks                   # 整库分块浏览（?namespace + ?offset/limit）
```

手工 FAQ 条目（kind=manual）与检索：

```
POST   /api/v1/knowledge/entries                  # 新建手工文本条目（admin）
PUT    /api/v1/knowledge/entries/{id}             # 编辑手工条目（仅 manual；先清旧向量再重灌）
POST   /api/v1/knowledge/search                   # 检索测试（body: namespace, query, top_k?, min_score?, advanced?）
```

## Admin · Agents（代码 + 动态混合注册，require_admin 写）

（[U03](updates/03-dynamic-agents.md)、[U11](updates/11-code-agent-transparency-state.md)、[U12](updates/12-agent-routing-clues.md)）

```
GET    /api/v1/admin/agents                 # 代码注册表 + DB 动态 Agent 合并（code 带出 system_prompt/registered_tools/custom_tool_names/mcp_tools；孤儿 code 行标 stale）
GET    /api/v1/admin/agents/{name}          # Agent 详情（代码/动态均含注册工具；stale 行回显 DB 镜像）
POST   /api/v1/admin/agents                 # 创建动态 Agent（重名/冲突→3002；校验 MCP 绑定、自定义工具与 namespace；可带 routing_keywords/routing_examples）
PUT    /api/v1/admin/agents/{name}          # 编辑动态 Agent（仅 source=dynamic 可编辑；routing_* 传入即更新，空串项自动剔除）
DELETE /api/v1/admin/agents/{name}          # 删除（在册代码 Agent 不可删→1001；代码已下线的孤儿行可删）
PATCH  /api/v1/admin/agents/{name}/toggle   # 启用/禁用（body: enabled；禁用后路由与解析真正不命中）
```

## Admin · Scenes（场景与嵌入配置，require_admin 写 / require_staff 读）

（[多场景开发文档](multi-scene-development.md) M2–M5）

```
GET    /api/v1/admin/scenes                         # 列表（含状态、Agent 数、路由模式、publish_key）
GET    /api/v1/admin/scenes/options                 # 启用中的场景下拉（聊天入口/资源归属回显）
POST   /api/v1/admin/scenes                         # 新建（code 全局唯一；默认场景标识保留）
GET    /api/v1/admin/scenes/{id}                    # 详情（含 widget_config/allowed_origins/api_key_set）
PATCH  /api/v1/admin/scenes/{id}                    # 编辑（默认场景只允许改展示名/描述）
PUT    /api/v1/admin/scenes/{id}/agents             # 绑定 Agent（全量替换，顺序即路由候选顺序）
GET    /api/v1/admin/scenes/{id}/agents             # 可绑定 Agent + 当前绑定状态
GET    /api/v1/admin/scenes/{id}/knowledge-bases    # 知识库归属弹窗（被其它场景占用的不可选）
PUT    /api/v1/admin/scenes/{id}/knowledge-bases    # 全量替换归属；默认场景只读
PUT    /api/v1/admin/scenes/{id}/widget             # 嵌入配置/允许来源/发布开关
POST   /api/v1/admin/scenes/{id}/rotate-key         # 轮换服务端 API key（明文仅本次响应）
DELETE /api/v1/admin/scenes/{id}                    # 删除（默认场景不可删；有关联会话等→3002）
```

`PUT /widget` 请求体（未传字段保持原值）：

```json
{
  "widget_config": {"title": "订单助手", "welcome": "您好", "theme_color": "#2b5aa0", "position": "right", "placeholder": "请输入您的问题", "show_branding": true},
  "allowed_origins": ["https://mall.example.com"],
  "allow_anonymous": true
}
```

- `widget_config` 归一化后全量替换：只保留已知键，文本限长，颜色只接受 `#RGB/#RRGGBB`，位置只接受 `left/right`；
- `allowed_origins` 只保留 `scheme://host[:port]`（粘贴带路径的页面地址会自动截断），非法来源 400/1001；
- `allow_anonymous=true` 时若场景尚未发布则生成 `pk_...`；已有 `publish_key` 保持不变；`false` 时清空（已嵌入页面立即失效）；
- 保存后立即失效 CORS 来源缓存，无需等待 TTL。

`POST /rotate-key` 返回 `{"api_key": "sk_...", "api_key_set": true}`。库里只存 sha256；再次轮换后旧 key 立即失效。场景详情只回显 `api_key_set`，不返回哈希或明文。

## Admin · Router（路由分发器，只读，require_admin）

（[U13](updates/13-router-agent-readonly.md)）路由 Agent 的定义在代码 + `.env` 中，**不入库也不可在线修改**，因此本组只有两个 GET（写方法返回 405）。

```
GET    /api/v1/admin/router            # 基本档案：职责/模型/意图上限/置信阈值/兜底候选与兜底应答模式（fallback_confidence、fallback_answer）/上下文与线索上限/提示词模板原文与占位符说明（editable 恒为 false）
GET    /api/v1/admin/router/prompt     # 当前生效提示词预览（按生效清单实时渲染）+ 每个候选 Agent 的线索与实际注入部分
```

## Admin · Custom Tools（代码自定义工具登记，require_admin 写 / require_staff 读）

（[U10](updates/10-custom-tool-management.md)）

```
GET    /api/v1/admin/custom-tools            # 扫描代码目录列出工具（含 registered/enabled/覆盖描述）
POST   /api/v1/admin/custom-tools            # 登记到 tools 表 type=custom（不在目录→1001；重名/MCP 占用→3002）
PATCH  /api/v1/admin/custom-tools/{name}/toggle  # 启用/禁用（body: enabled）
DELETE /api/v1/admin/custom-tools/{name}     # 取消登记（被动态 Agent 引用→1001）
```

## Admin · Canned Scripts（高频固定话术，require_admin 写 / require_staff 读）

（[U16](updates/16-canned-scripts-db.md)）命中即在路由与大模型之前逐字下发；运行时只读这张表（代码里无兜底规则，
空表/全部停用 = 该能力关闭）；规则只开放 `patterns`（问法主干，归一化后整句全等）与 `keywords`（包含即命中，
归一化后至少 2 字），**不开放正则**；两者全空→1001。列表长度上限 `patterns` 100 / `keywords` 60（超→422；
该上限必须高于种子规模，因为后台保存是把整份列表原样提交回来）。

```
GET    /api/v1/admin/canned-scripts            # 全量（含停用项），按 priority 升序，与运行时判定顺序一致
POST   /api/v1/admin/canned-scripts            # 新增（key 重复→3002；空规则/单字关键词/问法归一化后为空→1001）
PUT    /api/v1/admin/canned-scripts/{key}      # 更新（只改一侧时另一侧沿用已存值，避免改空规则）
PATCH  /api/v1/admin/canned-scripts/{key}/toggle  # 启用/停用（body: enabled）
DELETE /api/v1/admin/canned-scripts/{key}      # 删除（is_builtin 种子三条不可删→1001，只能停用）
POST   /api/v1/admin/canned-scripts/match      # 命中测试（body: query；不写库，返回 normalized/matched/key/reply_text，未命中即本轮走路由）
```

## Admin · MCP Servers（require_admin 写 / require_staff 读）

（[U05](updates/05-stdio-mcp-demo.md)）

```
GET    /api/v1/admin/mcp-servers                       # 列表（附 used_by_agents 反查）
POST   /api/v1/admin/mcp-servers                       # 新建（transport=sse|streamable_http|stdio；重名 3002）
PUT    /api/v1/admin/mcp-servers/{server_id}           # 更新配置/启停
DELETE /api/v1/admin/mcp-servers/{server_id}           # 删除（被动态 Agent 引用→1001）
POST   /api/v1/admin/mcp-servers/test                  # 连通性探测（不落库，返回 tool_count/tools）
GET    /api/v1/admin/mcp-servers/{server_id}/tools     # 按已存配置连接并列出全部工具（带 timeout）
```

## Admin · Tickets（通用工单，客服/IT 共用表，require_staff）

（[U06](updates/06-tickets-cross-round-memory.md)、[U17](updates/17-it-helpdesk-pack.md)，以 `ticket_no`（`TK-XXXXXXXX`）定位；IT 包写入 `category=it_*` 与 `metadata.pack=it_helpdesk`）

```
GET    /api/v1/admin/tickets                 # 列表（?user_id/status/category/urgency/keyword/date_from/date_to + 分页）
GET    /api/v1/admin/tickets/{ticket_no}     # 详情（含 updates 补录历史与 metadata；IT 工单带 pack=it_helpdesk）
PATCH  /api/v1/admin/tickets/{ticket_no}/status  # 流转状态（body: status, note?；note 追加进 updates）
POST   /api/v1/admin/tickets/{ticket_no}/notes   # 补录一条信息（与业务 update_ticket 同结构）
```

## Admin · Inquiries（跨客户咨询，只读，require_staff）

```
GET    /api/v1/admin/inquiries/sessions          # 跨客户会话列表（?user_id/status/last_route_to/date_from/date_to + 分页）
GET    /api/v1/admin/inquiries/sessions/export   # 导出 CSV（UTF-8 BOM，同过滤条件）
GET    /api/v1/admin/inquiries/sessions/{id}     # 会话详情（消息 + 每条回复的 trace_id）
```

## Admin · Traces（请求链路，只读，require_staff）

（[U04](updates/04-request-tracing.md)）

```
GET    /api/v1/admin/traces                  # 列表（不带 spans；?session_id/user_id/agent_name/tool_name/mcp_server/outcome/has_error/min_latency_ms/date_from/date_to + 分页）
GET    /api/v1/admin/traces/usage            # 近 N 天依赖使用分布（?field=tool_names|agent_names|mcp_server_names, days）
GET    /api/v1/admin/traces/sessions/{id}    # 某会话按轮次链路摘要（时间正序）
GET    /api/v1/admin/traces/{trace_ref}      # 单条详情（含 spans 时间线；trace_ref=行ID/HTTP trace_id/助手消息ID）
```

> 链路详情的 `spans` 中，`route.intent`（每个路由意图）除 `intent_index`/`confidence`/`reason`/`is_fallback` 外，
> 还带意图依据 `matched_keywords`（原句命中的路由关键词，**本地子串匹配、仅供参考**，路由实际由 LLM 语义判定）
> 与 `clue_basis`（`keyword_hit` / `keyword_miss` 配了关键词但未出现 / `no_keywords` 未配关键词），
> 见 [U15](updates/15-route-intent-evidence.md)。数据只存 `spans` JSONB，无额外列。

## Admin · Tools / Metrics

```
GET    /api/v1/admin/tools                  # 工具列表（require_staff）
GET    /api/v1/admin/metrics                # Prometheus 指标（require_admin，text/plain）
GET    /health                              # 存活探针
GET    /metrics                             # Prometheus 全局抓取端点
```

## SSE 事件类型（/chat/stream）

| event | data | 说明 |
|-------|------|------|
| `session` | `{session_id}` | 首帧会话 ID |
| `route` | `{agent_name, confidence, reason, intents, intent_total}` | 路由结果；`agent_name/confidence/reason` 为主意图，`intents` 为按主次排序的意图清单（最多 `ROUTER_MAX_INTENTS` 个，[U02](updates/02-multi-intent-routing.md)）；兜底意图额外带 `is_fallback: true`（[U14](updates/14-fallback-answer-path.md)）；每个意图额外带 `matched_keywords`/`clue_basis` 意图依据（[U15](updates/15-route-intent-evidence.md)） |
| `agent_start` | `{agent_name, intent_index, intent_total, confidence, reason}` | 一个意图分支开始应答 |
| `agent_end` | `{agent_name, intent_index, intent_total}` | 该意图分支应答结束 |
| `text` | `{content}` | 文本增量 |
| `tool_call` | `{id, name, args}` | 工具调用开始 |
| `tool_result` | `{id, name, result, error}` | 工具返回（[U09](updates/09-async-tool-execution.md) 真实执行） |
| `memory_write` | `{type, id}` | 记忆写入通知 |
| `handover` | `{reason, confidence}` | 转人工 |
| `done` | `{message_id, trace_id}` | 结束；`trace_id` 可在后台链路页定位本轮 |
| `error` | `{code, message}` | 错误 |

> 多意图：一条消息被识别为多个意图时，各业务 Agent **串行**执行，`intent_index > 0` 的分支前会先下发一段 `text`（`\n\n---\n\n`）作分块分隔；主意图置信度低于 `ROUTER_CONFIDENCE_THRESHOLD` 时整轮 `handover`，仅次要意图低于阈值则该分支直接丢弃。
>
> 例外（[U14](updates/14-fallback-answer-path.md)）：路由判定“不贴合任何 Agent”而给出的兜底意图（`is_fallback: true`，置信度为提示词约定的固定低值 0.3）**不受阈值影响**，默认由兜底 Agent 先应答；置 `ROUTER_FALLBACK_ANSWER=false` 可恢复“也转人工”的旧行为。

## 错误码（`app/core/exceptions.py`）

| code | HTTP | 异常 | 说明 |
|------|------|------|------|
| 0 | 200 | — | 成功 |
| 1001 | 400 | ParamError | 参数校验失败 |
| 2001 | 401 | Unauthorized | 未认证 |
| 2002 | 401 | TokenExpired | Token 过期 |
| 2003 | 403 | Forbidden | 无权限 |
| 2004 | 404 | AccountNotRegistered | 账号不存在，请先注册（客户登录引导） |
| 3001 | 404 | NotFound | 资源不存在 |
| 3002 | 409 | Conflict | 资源冲突（重名/仍被引用） |
| 3003 | 404 | SceneNotFound | 场景不存在（scene_code 错误） |
| 3004 | 404 | SceneDisabled | 场景已停用/归档 |
| 3005 | 409 | SceneNotConfigured | 场景未配置可用 Agent |
| 4001 | 400 | ToolParamError | 工具参数校验失败 |
| 4003 | 400 | UnsafeInput | 输入含不安全内容 |
| 4029 | 429 | RateLimited | 请求过于频繁 |
| 5001 | 500 | InternalError | 内部错误 |
| 5002 | 502 | LLMUnavailable | LLM 服务不可用 |
| 5003 | 503 | RagSearchFailed | RAGTool 检索失败 |
| 5004 | 504 | ToolTimeout | 工具调用超时 |
| 5005 | 500 | MemoryToolFailed | MemoryTool 调用失败 |
| 5006 | 500 | AgentNotRegistered | Agent 未注册 |
