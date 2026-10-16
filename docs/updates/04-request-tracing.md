# U04 · 请求链路追踪（request_traces）

> 状态：已落地 ｜ 影响面：新增表、采集器、编排/上下文埋点、后台 API、清理脚本

## 1. 背景与问题

一轮对话可能经过：路由 → 多业务 Agent → MCP 工具发现/调用 → 记忆检索 → RAG 检索 → 记忆归档。出问题时（答非所问、工具没调用、MCP 静默失败、上下文被预算丢弃）只靠日志难以定位。需要把「一轮请求的完整决策链」结构化落库并在后台可视化。

## 2. 数据模型（request_traces 表，一轮一行）

`models/entities.py::RequestTrace` / [init_db.sql](../../scripts/init_db.sql)：

- 关联：`trace_id`（对齐 structlog / `X-Trace-Id`）、`session_id`、`user_id`、`user_message_id`、`assistant_message_id`；
- **拍平的可过滤维度**（数组列 + GIN 索引，避免列表页解 JSONB）：`intent_count`、`agent_names`、`agent_sources`、`llm_models`、`mcp_server_names`、`tool_names`、`rag_namespaces`；
- 计数与结论：`memory_hit_count`、`rag_chunk_count`、`tool_call_count`、`tool_error_count`、`confidence`、`outcome`(answered/handover/error)、`handover_reason`、`error_code`、`error_message`、`ttft_ms`、`total_latency_ms`、`response_chars`；
- 明细时间线：`spans` JSONB。

索引：`trace_id`、`assistant_message_id`（咨询详情反查链路）、`session+created`、`user+created`、`outcome+created`，数组列 GIN（agents/tools/mcp）。

## 3. Span 类型（core/tracing.py）

| 常量 | type | 记录点 |
|------|------|--------|
| `SPAN_ROUTER` | `router.llm` | 路由 LLM 调用（含候选数、意图数、原始输出、是否兜底） |
| `SPAN_INTENT` | `route.intent` | 每个意图（index/confidence/reason） |
| `SPAN_MEMORY` | `context.memory` | 记忆检索（命中数/错误） |
| `SPAN_RAG` | `context.rag` | 各 namespace RAG 检索（chunk 数/最高分/错误） |
| — | `context.budget` | 上下文块因预算被丢弃（status=skipped） |
| `SPAN_RESOLVE` | `agent.resolve` | 意图→Agent 解析（source、声明的 MCP、namespace；未注册则 error） |
| `SPAN_BUILD` | `agent.build` | Agent 构建（模型、实际连上的 MCP、可用工具清单） |
| `SPAN_RUN` | `agent.run` | 单意图流式应答（响应字符数、空应答标 empty） |
| `SPAN_TOOL` | `tool.call` | 工具调用（call→result 跨事件配对，含入参/返回/错误） |
| `SPAN_PERSIST` | `memory.persist` | 记忆归档（importance、是否低于阈值 skipped） |

每 span 含 `span_id / parent_id / type / name / start_ms / duration_ms / status / attributes`。

## 4. 采集器 TraceCollector

- **纯内存累加、不做 IO**；落库由 ChatService 在流结束后一次完成；
- `enabled = 开关 && settings.trace_enabled`，所有方法在 disabled 时直接返回，调用方无需判空；
- `add()` 落即时 span；`start()/end()` 记录跨事件的 span（工具 call/result 分两次到达）；
- `add_unique(bucket, value)` 往拍平维度去重追加；
- `record_intents / record_tool_call / finish_tool_call / record_outcome` 为业务语义封装；
- `mark_first_token()` 记 TTFT，`mark_finished()` 记总耗时；
- `to_row()` 拍平成一行。

## 5. 埋点位置

- `Orchestrator.dispatch(..., collector)`：路由、意图、解析、构建、运行、工具、结局全程传入 collector；
- `ContextEngineer.build/persist_after_run(..., collector)`：记忆/RAG/预算/归档；
- `RouterAgent.route(..., collector)`：router.llm span（**保留原始输出**用于区分「模型判错」vs「解析失败」）；
- `ChatService.stream/reply`：创建 collector、串接 `route→set_last_route_to`、结束时 `_persist_trace`。

`agent.build` 的 `available_tools` 与 `agent.resolve` 的 `declared_mcp` 对比，可暴露「MCP 连上了但没展开出工具」这类静默失败。

## 6. 采样、脱敏与旁路

| 配置 | 默认 | 说明 |
|------|------|------|
| `TRACE_ENABLED` | true | 整体开关 |
| `TRACE_STORE_PAYLOAD` | true | 关闭时工具入参/返回只存 `<payload omitted:Nchars>` 类型摘要，不存原文 |
| `TRACE_PAYLOAD_MAX_CHARS` | 2000 | 入库前截断 |
| `TRACE_SAMPLE_RATE` | 1.0 | 正常应答按概率采样；`should_persist()` 保证 handover/error/带工具异常的轮次 **永远全量** |
| `TRACE_RETENTION_DAYS` | 30 | 供清理脚本取用 |

写入为 **旁路**：`_persist_trace` 失败只记日志，绝不影响对话。异常路径（服务端 Exception）也落链路；客户端断开（GeneratorExit）不落。

## 7. 后台 API（api/v1/admin_traces.py，只读，require_staff）

| 端点 | 关键参数 | 返回 |
|------|---------|------|
| `GET /admin/traces` | `session_id/user_id/agent_name/tool_name/mcp_server/outcome/has_error/min_latency_ms/date_from/date_to` + 分页 | 摘要列表（不含 spans，附 span_count） |
| `GET /admin/traces/usage` | `field=tool_names\|agent_names\|mcp_server_names`、`days(1~90)` | 近 N 天各依赖被多少轮覆盖（识别没人用/长期失败的依赖） |
| `GET /admin/traces/sessions/{id}` | — | 该会话按轮次的链路摘要（时间正序） |
| `GET /admin/traces/{trace_ref}` | `trace_ref`=行ID / HTTP trace_id / 助手消息ID | 单条详情（含 spans 时间线） |

`done` 事件带 `trace_id`，前端可据此跳转链路页。咨询详情用 `assistant_message_id` 反查本轮链路。

## 8. 前端与清理

- `admin-frontend/views/TracesView.vue` + `components/TraceTimeline.vue`：列表过滤 + span 时间线；
- `admin-frontend/views/InquiriesView.vue`：会话详情内嵌每条回复的链路跳转；
- `scripts/cleanup_traces.py`：按 `TRACE_RETENTION_DAYS`（`--days N` / `--dry-run`）删除过期链路，建议挂 cron。

## 9. 验证

`tests/test_tracing.py`、`test_admin_traces.py`、`test_chat_service_tracing.py`、`test_orchestrator_tracing.py`。
