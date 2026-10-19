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

## Chat（核心）

```
POST   /api/v1/chat/stream              # SSE 流式对话（current_customer）
POST   /api/v1/chat/message             # 非流式备用，返回 {data, trace_id}
```

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

## Admin · Tickets（客诉工单，require_staff）

（[U06](updates/06-tickets-cross-round-memory.md)，以 `ticket_no`（`TK-XXXXXXXX`）定位）

```
GET    /api/v1/admin/tickets                 # 列表（?user_id/status/category/urgency/keyword/date_from/date_to + 分页）
GET    /api/v1/admin/tickets/{ticket_no}     # 详情（含 updates 补录历史）
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
| 4001 | 400 | ToolParamError | 工具参数校验失败 |
| 4003 | 400 | UnsafeInput | 输入含不安全内容 |
| 4029 | 429 | RateLimited | 请求过于频繁 |
| 5001 | 500 | InternalError | 内部错误 |
| 5002 | 502 | LLMUnavailable | LLM 服务不可用 |
| 5003 | 503 | RagSearchFailed | RAGTool 检索失败 |
| 5004 | 504 | ToolTimeout | 工具调用超时 |
| 5005 | 500 | MemoryToolFailed | MemoryTool 调用失败 |
| 5006 | 500 | AgentNotRegistered | Agent 未注册 |
