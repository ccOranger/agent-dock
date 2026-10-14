# 运维手册（Runbook）

## 1. 服务清单

| 服务 | 端口 | 启动方式 |
|------|------|---------|
| cs-backend | 8000 | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| cs-frontend（开发） | 5173 | `pnpm dev` |
| PostgreSQL 15 + pgvector | 5432 | 系统服务 |
| Redis 7 | 6379 | 系统服务 |

## 2. 启动顺序

1. PostgreSQL / Redis 就绪
2. `scripts/init_db.sh`（首次）
3. 后端：确认 `.env`（LLM_API_KEY、DATABASE_URL、REDIS_URL、JWT_SECRET）→ `uvicorn app.main:app`
4. 前端：`pnpm dev`（开发）或 `pnpm build` 后由 Nginx 托管 `dist/`

启动日志中应看到 HelloAgents 初始化三步（工具 → 注册 → Agent）与 Agent 自动登记。

## 3. 健康检查与监控

- `GET /health` — 存活探针
- `GET /metrics` — Prometheus 指标；关键面板：
  - `cs_chat_requests_total{status}` 错误率 > 5%（5min）告警
  - `cs_chat_ttft_seconds` P95 > 5s 告警
  - `cs_router_confidence` 低置信度比例 > 30% 告警
  - `cs_active_sse_connections` > 400 告警（上限 500）

## 4. 常见故障

| 症状 | 排查 |
|------|------|
| 启动报 `DB 中以下 Agent 未在代码注册` | agents 表残留旧记录，删除对应行或补齐代码注册 |
| SSE 无 `route` 事件 | 检查 LLM_API_KEY / 路由模型可达性；看日志 `路由结果解析失败` |
| 多意图消息只应答一个诉求 | 看日志 `[route] ... intents=N`：`intents=1` 是模型没拆意图（调提示词/换路由模型）；`intents≥2` 但只走一个分支则受 `ROUTER_MAX_INTENTS` 或 `ROUTER_CONFIDENCE_THRESHOLD` 限制（低于阈值的次要意图会被丢弃） |
| 后台看不到某轮链路 | 确认 `TRACE_ENABLED=true` 且 `request_traces` 表已建（新库需跑 `scripts/rebuild_and_seed.py`）；`TRACE_SAMPLE_RATE<1` 时正常应答会被采样丢弃，但转人工/异常/工具报错轮始终保留 |
| 链路写入失败告警 | `链路写入失败（不阻塞主流程）`：多为 `request_traces` 缺表/字段与 ORM 不一致；写库是旁路，不影响对话，补跑建表脚本即可 |
| 上传文档 status=failed | `documents.error_message`；确认 RAG_KB_ROOT 目录可写 |
| MemoryTool 检索为空 | 首轮对话属正常；确认 user_id 一致、WORKING_MEMORY_TTL |
| 401 频繁 | 时钟偏差导致 JWT exp 判定失败；校时 |
| Agent 无 MCP 工具 / 日志 `MCP 已连接但远端未返回任何工具` 或工具数为 0 | 常见于 **Windows GBK 控制台**：hello_agents 的 MCP 客户端用裸 `print` 输出 emoji 会抛 `UnicodeEncodeError` 并被静默吞掉。`app/main.py` 已在启动时把 stdout/stderr 切到 UTF-8；若手动跑脚本验证，先 `$env:PYTHONIOENCODING="utf-8"` |
| MCP 服务连不上 / 注册超时 | 确认 `tools` 表里 `mcp_logistics`/`mcp_refund` 的 `config` 为 `transport=stdio`（旧库遗留假域名 `http://mcp-*:9000/sse` 会连不上超时）；重跑 `scripts/seed_data.py` 或 `rebuild_and_seed.py` 即可覆盖为 stdio |

## 5. 数据维护

- `memories` / `documents` 仅为 MemoryTool / RAGTool 的归档镜像，运行时真相在 HelloAgents 本地持久化
- `request_traces`（请求链路）按轮次增长、带 spans 时间线，需设保留期：`python scripts/cleanup_traces.py`（默认取 `TRACE_RETENTION_DAYS`，可 `--days N` / `--dry-run`）；建议挂 cron 每日执行
- 工具入参/返回含客户数据：合规敏感场景设 `TRACE_STORE_PAYLOAD=false`（只留类型与字节数，不存原文）
- 迁移：`alembic upgrade head`（回滚 `alembic downgrade -1`，提交前双向测试）
- 知识重建：管理页 → 重建索引，或 `POST /api/v1/knowledge/documents/{id}/reindex`

## 6. 本地 MCP 演示服务（物流 / 退款）与固定数据

`mcp_logistics` / `mcp_refund` 已实现为**本地 FastMCP 服务**（`backend/mcp_servers/*.py`），
以 **stdio 子进程**方式由 `MCPTool` 拉起，**不需要任何端口或外部域名**：

- 服务脚本：`backend/mcp_servers/logistics_server.py`、`refund_server.py`
- 固定数据源（单一真相）：`backend/app/tools/custom/mock_data.py`；
  进程内自定义工具（`query_order`/`query_logistics`/`submit_refund` 等）与 MCP 服务共用同一份
- 配置落库：`tools` 表 `config = {transport: stdio, command: <venv python>, args: [<脚本绝对路径>], timeout_seconds: 20}`
  （由 `scripts/seed_data.py` 写入，并对旧数据做 upsert 覆盖）

MCP 工具展开后暴露给模型的名字带前缀：`mcp_logistics_query_logistics`、`mcp_logistics_query_order_logistics_status`、`mcp_refund_submit_refund`、`mcp_refund_query_refund`。

可演示的固定订单号：

| 订单号 | 状态 | 备注 |
|--------|------|------|
| SO20240001 | 已发货 | 有物流（顺丰，运输中） |
| SO20240002 | 已支付 | 未发货，无物流；可改备注/申请退款 |
| SO20240003 | 已签收 | 有物流（中通） |
| SO20240004 | 处理中 | 可申请退款 |
| SO20240005 | 已取消 | 已有退款单 RF20240001；退款会被拒 |
| SO20240006 | 已发货 | 有物流（京东） |

单独自检某个 MCP 服务（不经过后端，直接列工具）：

```powershell
cd d:\project\smart-cs\backend; $env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -c "import asyncio,sys; from hello_agents.protocols.mcp.client import MCPClient;\nasync def m():\n    async with MCPClient([sys.executable,'mcp_servers/logistics_server.py']) as c:\n        print(await c.list_tools())\nasyncio.run(m())"
```

> 退款提交仅在内存累加，进程重启回到初始数据；改单字段限于 `address` / `remark`（白名单）。

## 7. 安全

- 生产必须更换 `JWT_SECRET`、seed 账号密码
- 反向代理需关闭 SSE 缓冲（Nginx：`proxy_buffering off;`）并设 `proxy_read_timeout ≥ 300s`
- 限流：单用户对话 20/min、上传 10/h、登录 10/min/IP
