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
| 上传文档 status=failed | `documents.error_message`；确认 RAG_KB_ROOT 目录可写 |
| MemoryTool 检索为空 | 首轮对话属正常；确认 user_id 一致、WORKING_MEMORY_TTL |
| 401 频繁 | 时钟偏差导致 JWT exp 判定失败；校时 |

## 5. 数据维护

- `memories` / `documents` 仅为 MemoryTool / RAGTool 的归档镜像，运行时真相在 HelloAgents 本地持久化
- 迁移：`alembic upgrade head`（回滚 `alembic downgrade -1`，提交前双向测试）
- 知识重建：管理页 → 重建索引，或 `POST /api/v1/knowledge/documents/{id}/reindex`

## 6. 安全

- 生产必须更换 `JWT_SECRET`、seed 账号密码
- 反向代理需关闭 SSE 缓冲（Nginx：`proxy_buffering off;`）并设 `proxy_read_timeout ≥ 300s`
- 限流：单用户对话 20/min、上传 10/h、登录 10/min/IP
