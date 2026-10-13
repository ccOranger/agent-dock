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

GET    /health                         # 健康检查
GET    /metrics                        # Prometheus 全局抓取端点
```

## SSE 事件类型

| event | data | 说明 |
|-------|------|------|
| `session` | `{session_id}` | 首帧会话 ID |
| `route` | `{agent_name, confidence, reason}` | 路由结果 |
| `text` | `{content}` | 文本增量 |
| `tool_call` | `{id, name, args}` | 工具调用开始 |
| `tool_result` | `{id, name, result, error}` | 工具返回 |
| `memory_write` | `{type, id}` | 记忆写入通知 |
| `handover` | `{reason, confidence}` | 转人工 |
| `done` | `{message_id, usage}` | 结束 |
| `error` | `{code, message}` | 错误 |

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
