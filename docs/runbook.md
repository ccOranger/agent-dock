# 运维手册（Runbook · v2.0）

## 1. 服务清单

| 服务 | 端口 | 启动方式 | 必需 |
|------|------|---------|------|
| cs-backend | 8000 | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | 是 |
| frontend（客户对话端，Vite 开发） | 5173 | `npm run dev` | 按需 |
| admin-frontend（管理后台，Vite 开发） | 5174 | `npm run dev` | 按需 |
| PostgreSQL 14+ | 5432 | 系统服务 / 云托管 | 是（业务数据） |
| Redis 7 | 6379 | 系统服务 | 是（工作记忆/缓存） |
| Qdrant | 6333 | 系统服务 / 云托管 | 是（记忆 + RAG 向量真相） |
| Neo4j | 7687 | 系统服务 | 否（仅启用 `semantic` 语义记忆时必需） |

> **向量不落 PostgreSQL**：HelloAgents 的记忆（episodic/perceptual/semantic）与 RAG 分块向量都存在 **Qdrant**（`QDRANT_COLLECTION`，默认 `hello_agents_vectors`）。`documents`/`memories` 表只是**归档镜像**，运行时以 Qdrant 为准（见 §5）。
> **semantic 记忆硬依赖 Neo4j**：`MEMORY_TYPES` 默认 `working,episodic,perceptual`（不含 `semantic`）。未部署 Neo4j 时若加入 `semantic`，启动会因连接失败中断。部署 Neo4j 后置 `MEMORY_TYPES=working,episodic,semantic,perceptual` 并填 `NEO4J_*` 即可启用。

## 2. 一键启动 / 停止（Windows 开发环境）

```powershell
# 拉起后端 + 客户前端(5173) + 管理后台(5174)，各占独立 PowerShell 窗口
.\scripts\start_all.ps1              # 依赖已就绪时直接启动
.\scripts\start_all.ps1 -Install     # 首次：先建后端 venv 装依赖 + 前端 npm install 再启动
.\scripts\stop_all.ps1               # 关闭上述服务窗口
```

- 脚本会在每个窗口先设 `$env:PYTHONIOENCODING="utf-8"`（规避 Windows GBK 控制台下 hello-agents 打印 emoji 崩溃，见 §4）。
- 请用 **`http://localhost:5173 / :5174`** 访问，不要用 `127.0.0.1`——Vite 默认可能只监听 IPv6 `::1`。

## 3. 从零手工启动

1. PostgreSQL / Redis / Qdrant（如需 semantic 再加 Neo4j）就绪。
2. 建库：本机 `bash scripts/init_db.sh`；云库推荐直接跑 `python scripts/rebuild_and_seed.py`（单往返 simple query，规避连接池代理断连）。
3. 后端：确认 `backend/.env`（见 §8）→ 在 `backend` 目录 `python ..\scripts\seed_data.py`（建表 + 灌种子，幂等）→ `uvicorn app.main:app`。
4. 前端：`cd frontend; npm install; npm run dev`；管理后台：`cd admin-frontend; npm install; npm run dev`。

启动日志应看到 HelloAgents 初始化（工具 → 注册 → Agent）与 Agent 自动登记；`app/main.py` 会把 stdout/stderr 切到 UTF-8。

## 4. 健康检查与监控

- `GET /health` — 存活探针
- `GET /metrics` — Prometheus 指标；关键面板：
  - `cs_chat_requests_total{status}` 错误率 > 5%（5min）告警
  - `cs_chat_ttft_seconds` P95 > 5s 告警
  - `cs_router_confidence` 低置信度比例 > 30% 告警
  - `cs_active_sse_connections` > 400 告警（上限 500）

## 5. 常见故障

| 症状 | 排查 |
|------|------|
| 新建的动态 Agent 总路由不到 | 后台「路由分发器」→「候选 Agent 清单」定位原因：不在表里 = 被禁用/未启用；在表里但「线索完整度 = 仅描述」= 没填 `routing_keywords`，提示词里只有一行适用范围；填了多条却不命中 = 超出前 12 个未注入，或关键词/典型问法与客户真实说法不贴合（[U12](updates/12-agent-routing-clues.md)） |
| 启动日志告警「DB 中存在未在代码注册的 code Agent（已失效）」 | 代码已下线但 agents 表残留 `source=code` 行；**不再阻断启动**，该类行在后台 Agent 列表标「已失效」，可直接在后台删除（见 [U11](updates/11-code-agent-transparency-state.md)） |
| 启动因连接失败中断，日志涉及 Neo4j | `MEMORY_TYPES` 含 `semantic` 但未部署 Neo4j；去掉 `semantic` 或起 Neo4j |
| SSE 无 `route` 事件 | 检查 LLM_API_KEY / 路由模型可达性；看日志 `路由结果解析失败`；**例外**：命中固定话术的轮次本来就没有 `route` 事件，链路里只有一个 `canned.script` span（[U16](updates/16-canned-scripts-db.md)） |
| 寒暄/「你是谁」这类提问直接转人工、无任何应答 | 先看链路：这三类正常应由固定话术在路由之前逐字应答（[U16](updates/16-canned-scripts-db.md)），出现 `handover` 说明对应话术被停用、被删掉，或表里压根没数据（代码里无兜底，新库必须跑 `scripts/seed_data.py`）：后台「固定话术」页用命中测试确认。走路由的轮次才是该轮被判「不贴合任何 Agent」→ 兜底意图（置信度固定 0.3）低于阈值即整轮 `handover(low_confidence)`：确认 `ROUTER_FALLBACK_ANSWER=true`（默认）且兜底 Agent 的 `routing_keywords` 已认领元问题（[U14](updates/14-fallback-answer-path.md)）；链路页 `route.intent` span 的 `is_fallback` 可辨识兜底轮 |
| 后台改了固定话术却不生效 | 运行时只读 `canned_scripts` 表（代码里无兜底）：先查该行的 `enabled` 与 `reply_text`（正文为空视为未配，会被丢弃）；日志 `固定话术读取失败，本轮跳过话术判定` 说明表缺失/读挂，此时话术完全不生效（对话不受影响），跑 `scripts/seed_data.py` 补建表与种子 |
| 后台编辑固定话术保存报 422 | 多为规则列表超长：`patterns` 上限 100、`keywords` 上限 60（探听种子已占 32 个关键词，所以上限不能低于种子规模，否则改一个标点也被拒）；页面会显示当前条数，弹窗现在会把后端 `detail` 转成「字段名: 原因」，按字段定位是哪侧超了（[U16](updates/16-canned-scripts-db.md) §8） |
| 客户真实业务诉求被一句模板打发掉（误吞） | 误吞只可能来自 `keywords`（包含即命中）：后台「固定话术」页删掉过宽的词，新增时用命中测试先拿业务问法验一句；问法（`patterns`）是全等语义，不会吞掉带后半句的复合提问 |
| 多意图消息只应答一个诉求 | 看日志 `[route] ... intents=N`：`intents=1` 是模型没拆意图（调提示词/换路由模型）；`intents≥2` 但只走一个分支则受 `ROUTER_MAX_INTENTS` 或 `ROUTER_CONFIDENCE_THRESHOLD` 限制（低于阈值的次要意图会被丢弃） |
| 想知道「这句凭什么判给这个 Agent」/ 路由判错 Agent | 链路详情 `route.intent` 的「意图依据」：`keyword_hit` 看命中词（本地子串匹配，仅参考，不是模型真实判据）；`keyword_miss` 说明靠语义判的，应去 Agent 管理补与客户真实说法贴合的关键词；`no_keywords` 说明该 Agent 压根没配线索（[U15](updates/15-route-intent-evidence.md)） |
| 工具「调了却没落库」/模型自演假结果 `[TOOL_CALL...]` | 自定义工具是 `async run(**kwargs)`，必须由 `orchestrator._execute_tool` 经 `run_coroutine_threadsafe` 回投主 loop 执行（[U09](updates/09-async-tool-execution.md)）；勿绕回框架同步 `_execute_tool_call` |
| 后台看不到某轮链路 | 确认 `TRACE_ENABLED=true` 且 `request_traces` 表已建（新库需跑 `scripts/rebuild_and_seed.py`）；`TRACE_SAMPLE_RATE<1` 时正常应答会被采样丢弃，但转人工/异常/工具报错轮始终保留 |
| 链路写入失败告警 | `链路写入失败（不阻塞主流程）`：多为 `request_traces` 缺表/字段与 ORM 不一致；写库是旁路，不影响对话，补跑建表脚本即可 |
| 客户登录报「账号不存在」 | 分表后客户只在 `customer_users` 登录（`/auth/customer/login`）；账号不存在返回 `2004`，前端应引导 `/auth/customer/register` 自助注册（[U07](updates/07-staff-customer-auth-split.md)） |
| 后台登录 401 | 后台走 `/auth/staff/login`（`staff_users`），admin/agent 角色；客户账号无法登录后台，反之亦然 |
| 上传文档 status=failed | `documents.error_message`；确认 `RAG_KB_ROOT` 目录可写、Qdrant 可达 |
| 检索恒空 / `chunk_count` 恒 0 | namespace 未透传或 `RAGTool.run` 返回字符串未解析；已由 `_parse_rag_result` + 结构化 `search` 修复（[U08](updates/08-knowledge-base-admin.md) §11） |
| MemoryTool 检索为空 | 首轮对话属正常；确认 user_id 一致、`WORKING_MEMORY_TTL` |
| 401 频繁 | 时钟偏差导致 JWT exp 判定失败；校时 |
| Agent 无 MCP 工具 / 日志 `MCP 已连接但远端未返回任何工具` 或工具数为 0 | 常见于 **Windows GBK 控制台**：hello_agents 的 MCP 客户端用裸 `print` 输出 emoji 会抛 `UnicodeEncodeError` 并被静默吞掉。`app/main.py` 已在启动时把 stdout/stderr 切到 UTF-8；若手动跑脚本验证，先 `$env:PYTHONIOENCODING="utf-8"` |
| MCP 服务连不上 / 注册超时 | 确认 `tools` 表里 `mcp_logistics`/`mcp_refund` 的 `config` 为 `transport=stdio`（旧库遗留假域名 `http://mcp-*:9000/sse` 会连不上超时）；重跑 `scripts/seed_data.py` 或 `rebuild_and_seed.py` 即可覆盖为 stdio |

## 6. 数据维护与重建策略

- `memories` / `documents` 仅为 MemoryTool / RAGTool 的归档镜像，**运行时真相在 Qdrant（向量）+ Neo4j（语义，若启用）**；删文档会先清 Qdrant 向量再删 DB。
- **开发阶段 schema 重置**（本项目 schema 频繁变动，如 `users` 拆为 `staff_users`/`customer_users`、工单迁到 `tickets`）：改 ORM 后无需手写增量迁移，直接全量重建（⚠️ 清空业务数据，仅限开发）：
  ```powershell
  cd backend; .\.venv\Scripts\python.exe ..\scripts\rebuild_and_seed.py
  # DROP 旧表（含遗留 users）→ create_all 建表 → 灌种子；单往返 simple query 适配云库代理
  ```
- **脚本清单**（`scripts/`）：
  | 脚本 | 作用 |
  |------|------|
  | `init_db.sql` | 全量 schema（已去除 `uuid-ossp`/`pgvector` 依赖，UUID 用内置 `gen_random_uuid()`） |
  | `init_db.sh` | 本机一键建库 |
  | `seed_data.py` | 种子定义：建表 + 初始人员/客户/Agent/工具/示例文档/MCP 配置（幂等，可对旧数据 upsert） |
  | `rebuild_and_seed.py` | 开发阶段全量重建 + 灌种子（云库友好） |
  | `seed_memory_via_chat.py` | 模拟对话造记忆测试数据 |
  | `cleanup_traces.py` | 清理过期链路（见下） |
  | `start_all.ps1` / `start_all.bat` / `stop_all.ps1` | 一键起停三服务 |
- `request_traces`（请求链路）按轮次增长、带 spans 时间线，需设保留期：`python scripts/cleanup_traces.py`（默认取 `TRACE_RETENTION_DAYS`，可 `--days N` / `--dry-run`）；建议挂 cron 每日执行。
- 工具入参/返回含客户数据：合规敏感场景设 `TRACE_STORE_PAYLOAD=false`（只留类型与字节数，不存原文）。
- 知识重建：管理页 → 重建索引，或 `POST /api/v1/knowledge/documents/{id}/reindex`。

## 7. 本地 MCP 演示服务（物流 / 退款）与固定数据

`mcp_logistics` / `mcp_refund` 已实现为**本地 FastMCP 服务**（`backend/mcp_servers/*.py`），以 **stdio 子进程**方式由 `MCPTool` 拉起，**不需要任何端口或外部域名**（[U05](updates/05-stdio-mcp-demo.md)）：

- 服务脚本：`backend/mcp_servers/logistics_server.py`、`refund_server.py`
- 固定数据源（单一真相）：`backend/app/tools/custom/mock_data.py`；进程内自定义工具（`query_order`/`query_logistics`/`submit_refund` 等）与 MCP 服务共用同一份
- 配置落库：`tools` 表 `config = {transport: stdio, command: <venv python>, args: [<脚本绝对路径>], timeout_seconds: 20}`（`seed_data.py` 写入并 upsert 覆盖旧数据）
- 跨环境路径解析：`command`/`args` 由加载器解析为绝对路径，工作目录无关。

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
> 后台也可视化探测：`POST /api/v1/admin/mcp-servers/test`（临时配置探测）、`GET /api/v1/admin/mcp-servers/{id}/tools`（按已存配置列工具）。

## 8. 关键配置（backend/.env 摘录，全量见 .env.example）

| 变量 | 默认 | 说明 |
|------|------|------|
| `DATABASE_URL` | postgresql+asyncpg://... | 业务库（asyncpg） |
| `REDIS_URL` / `WORKING_MEMORY_TTL` | redis://localhost:6379/0 / 1800 | 工作记忆/缓存 |
| `QDRANT_URL` / `QDRANT_API_KEY` / `QDRANT_COLLECTION` | 空(回退 localhost:6333) / / hello_agents_vectors | 记忆 + RAG 向量库 |
| `MEMORY_TYPES` | working,episodic,perceptual | 加 `semantic` 需 Neo4j |
| `LLM_*` / `EMBED_*` | — | 框架直接读环境变量（OpenAI 兼容端点，如 DashScope） |
| `ROUTER_MODEL` / `ROUTER_CONFIDENCE_THRESHOLD` / `ROUTER_MAX_INTENTS` / `ROUTER_FALLBACK_ANSWER` | deepseek-flash / 0.5 / 2 / true | 路由模型需与 LLM provider 匹配；`ROUTER_MAX_INTENTS=1` 关闭多意图；`ROUTER_FALLBACK_ANSWER=false` 时「不贴合任何 Agent」的兜底轮恢复为直接转人工（[U14](updates/14-fallback-answer-path.md)）。路由分发器不入库、不可后台修改，只读查看见「路由分发器」页（[U13](updates/13-router-agent-readonly.md)） |
| `RAG_KB_ROOT` / `RAG_DOC_ARCHIVE_ROOT` | ./data/knowledge_bases / ./data/doc_archives | RAG 库根 / 原文归档（后台下载） |
| `TRACE_ENABLED` / `TRACE_STORE_PAYLOAD` / `TRACE_SAMPLE_RATE` / `TRACE_RETENTION_DAYS` | true / true / 1.0 / 30 | 链路采集开关/存原文/采样/保留 |
| `JWT_SECRET` / `ACCESS_TOKEN_TTL` / `REFRESH_TOKEN_TTL` | change_me_in_prod / 7200 / 604800 | 生产务必换密钥 |

## 9. 安全

- 生产必须更换 `JWT_SECRET`、seed 账号密码（admin / service01 / alice，见 README）。
- 反向代理需关闭 SSE 缓冲（Nginx：`proxy_buffering off;`）并设 `proxy_read_timeout ≥ 300s`。
- CORS：`ALLOWED_ORIGINS` 默认放行 5173/5174（含 127.0.0.1 变体），生产改为实际域名白名单。
- 限流：单用户对话 20/min、上传 10/h、登录 10/min/IP。
- 人员/客户分表：后台接口一律 `require_staff`（agent/admin），写操作多为 `require_admin`；客户接口仅能访问本人会话/记忆。
