# API 速查（v1.3）

Base URL：`/api/v1`，响应统一为 `{ "code": 0, "message": "ok", "data": {...} }`。
Swagger：`http://localhost:8000/docs`

```
POST   /api/v1/auth/login              # 登录，返回双 Token
POST   /api/v1/auth/refresh            # 刷新 Token
GET    /api/v1/auth/me                 # 当前用户

POST   /api/v1/chat/stream             # SSE 流式对话（核心）
POST   /api/v1/chat/message            # 非流式备用

GET    /api/v1/sessions                # 会话列表（分页）
POST   /api/v1/sessions                # 新建会话
GET    /api/v1/sessions/{id}           # 会话详情
PATCH  /api/v1/sessions/{id}           # 更新标题/状态
DELETE /api/v1/sessions/{id}           # 删除会话
GET    /api/v1/sessions/{id}/messages  # 历史消息（分页）

GET    /api/v1/memory/me               # 自己的记忆
GET    /api/v1/memory/users/{user_id}  # 某用户记忆（agent/admin）
POST   /api/v1/memory/search           # 语义检索（agent/admin）
PATCH  /api/v1/memory/{id}             # 修正记忆（agent/admin）
DELETE /api/v1/memory/{id}             # 遗忘记忆（agent/admin）

POST   /api/v1/knowledge/documents         # 上传文档（multipart，admin）
GET    /api/v1/knowledge/documents         # 文档列表
DELETE /api/v1/knowledge/documents/{id}    # 删除文档
POST   /api/v1/knowledge/documents/{id}/reindex  # 重建索引
POST   /api/v1/knowledge/search            # 检索测试

GET    /api/v1/admin/agents            # Agent 列表（代码注册表+DB 合并）
GET    /api/v1/admin/agents/{name}     # Agent 详情
PATCH  /api/v1/admin/agents/{name}/toggle  # 启用/禁用
GET    /api/v1/admin/tools             # 工具列表
GET    /api/v1/admin/metrics           # Prometheus 指标（admin）

GET    /api/v1/admin/inquiries/sessions     # 跨客户咨询列表（agent/admin）
GET    /api/v1/admin/inquiries/sessions/{id} # 会话详情（含消息 + 每条回复的 trace_id）
GET    /api/v1/admin/traces                 # 请求链路列表（多维过滤）
GET    /api/v1/admin/traces/usage           # 工具/Agent/MCP 使用分布
GET    /api/v1/admin/traces/sessions/{id}   # 某会话按轮次的链路
GET    /api/v1/admin/traces/{trace_ref}      # 单条链路详情（含 spans 时间线）

GET    /health                         # 健康检查
GET    /metrics                        # Prometheus 全局抓取端点
```

## SSE 事件类型

| event | data | 说明 |
|-------|------|------|
| `session` | `{session_id}` | 首帧会话 ID |
| `route` | `{agent_name, confidence, reason, intents, intent_total}` | 路由结果；`agent_name/confidence/reason` 为主意图，`intents` 为按主次排序的意图清单（最多 `ROUTER_MAX_INTENTS` 个） |
| `agent_start` | `{agent_name, intent_index, intent_total, confidence, reason}` | 一个意图分支开始应答 |
| `agent_end` | `{agent_name, intent_index, intent_total}` | 该意图分支应答结束 |
| `text` | `{content}` | 文本增量 |
| `tool_call` | `{id, name, args}` | 工具调用开始 |
| `tool_result` | `{id, name, result, error}` | 工具返回 |
| `memory_write` | `{type, id}` | 记忆写入通知 |
| `handover` | `{reason, confidence}` | 转人工 |
| `done` | `{message_id, trace_id}` | 结束；`trace_id` 可用于在后台链路页定位本轮 |
| `error` | `{code, message}` | 错误 |

> 多意图：一条消息被识别为多个意图时，各业务 Agent **串行**执行，`intent_index > 0` 的分支前会先下发一段 `text`（`\n\n---\n\n`）作分块分隔；主意图置信度低于 `ROUTER_CONFIDENCE_THRESHOLD` 时整轮 `handover`，仅次要意图低于阈值则该分支直接丢弃。

## 请求链路追踪（admin/traces）

一轮对话 = `request_traces` 表的一行，记录本轮经过的业务 Agent / MCP 服务 / 工具 / RAG / 记忆与耗时、结局。高频过滤维度拍平为列（`agent_names`、`tool_names`、`mcp_server_names` 等数组列，GIN 索引命中），明细时间线存 `spans` JSONB（`router.llm` / `route.intent` / `agent.resolve` / `agent.build` / `tool.call` / `context.memory` / `context.rag` / `memory.persist` 等，每步含 `start_ms`、`duration_ms`、`status`、`attributes`）。

| 端点 | 关键参数 | 返回 |
|------|---------|------|
| `GET /admin/traces` | `session_id/user_id/agent_name/tool_name/mcp_server/outcome/has_error/min_latency_ms/date_from/date_to` + 分页 | 摘要列表（不含 spans，附 `span_count`） |
| `GET /admin/traces/usage` | `field=tool_names\|agent_names\|mcp_server_names`、`days` | 近 N 天各依赖被多少轮覆盖（识别没人用/长期失败的依赖） |
| `GET /admin/traces/sessions/{id}` | — | 该会话按轮次的链路摘要（时间正序） |
| `GET /admin/traces/{trace_ref}` | `trace_ref`=行ID / HTTP trace_id / 助手消息ID | 单条详情（含 spans 时间线） |

> 采集为旁路：写库失败只记日志、绝不影响对话。`TRACE_ENABLED=false` 整体关闭；`TRACE_STORE_PAYLOAD=false` 时工具入参/返回只留类型摘要不存原文；`TRACE_SAMPLE_RATE<1` 时正常应答按概率采样，但 `handover`/`error`/带工具异常的轮次永远全量保留，便于事后排查。

## 错误码

| code | HTTP | 说明 | | code | HTTP | 说明 |
|------|------|------|-|------|------|------|
| 0 | 200 | 成功 | | 4003 | 400 | 输入含不安全内容 |
| 1001 | 400 | 参数校验失败 | | 4029 | 429 | 请求过于频繁 |
| 2001 | 401 | 未认证 | | 5001 | 500 | 内部错误 |
| 2002 | 401 | Token 过期 | | 5002 | 502 | LLM 服务不可用 |
| 2003 | 403 | 无权限 | | 5003 | 503 | RAGTool 检索失败 |
| 3001 | 404 | 资源不存在 | | 5004 | 504 | 工具调用超时 |
| 4001 | 400 | 工具参数校验失败 | | 5005 | 500 | MemoryTool 调用失败 |
| | | | | 5006 | 500 | Agent 未注册 |
