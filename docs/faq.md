# 常见问题（FAQ）

> 按现象查。更完整的排障表在 [运维手册](runbook.md) §5，配置项含义见 [配置说明](configuration.md)。

## 部署与启动

### 根 `.env` 和 `backend/.env` 有什么区别？

- **根 `.env`**：给 `docker compose` 用。Compose 用它做变量插值，并作为 backend 容器的 `env_file`。Docker 部署只改这一份。
- **`backend/.env`**：给不用 Docker 的本地开发用。后端从**当前工作目录**加载 `.env`，所以要在 `backend/` 下启动。

两份内容可以不同：容器内的 `DATABASE_URL` / `REDIS_URL` / `QDRANT_URL` 会被 Compose 固定为服务名，根 `.env` 里写 `localhost` 不影响容器。

### 管理员密码丢了怎么办？

日志只在**首次创建管理员**时打印随机密码。确认丢失后，用初始化向导重置（在 backend 容器内执行）：

```bash
docker compose exec -T backend python -m app.setup \
  --username admin --password 'NewStrongPass123' --reset-password \
  --skip-llm-check --env-file /tmp/setup.env
```

说明：

- `--reset-password` 才会覆盖已存在账号的密码；不带该参数时只创建新账号。
- 容器镜像里没有仓库根 `.env`（已在 `.dockerignore` 排除），`--env-file` 只是向导写入 JWT 的落点；写到 `/tmp/setup.env` 不影响运行中的配置（JWT 由 Compose 注入），命令里关于 JWT 的提示可以忽略。
- 生产环境重置后请记录操作并检查是否有异常登录。

### 8080 / 8000 端口被占用

在根 `.env` 改：

```ini
WEB_PORT=18080
BACKEND_PORT=18000
```

然后 `docker compose up -d`。8000 只绑定 `127.0.0.1`，通常不会与外部服务冲突。

### `web` 一直 unhealthy 或访问 `/admin/` 报 502

先看 backend 是否 healthy：

```bash
docker compose ps
docker compose logs --tail=100 backend
```

backend 未就绪时 web 不会 healthy。Nginx 通过 Docker 内置 DNS 动态解析 backend，backend 重建换 IP 后无需重启 web。若 backend healthy 仍 502，检查 `docker/nginx.conf` 是否被本地改动。

### 改了 `POSTGRES_PASSWORD` 后 backend 连不上数据库

该变量只在**数据卷首次初始化**时生效。已有 `pgdata` 时密码没变，但 Compose 用新密码去连，就会认证失败。两种处理：

```bash
# 进库改密码
docker compose exec postgres psql -U cs_user -d cs_db -c "ALTER USER cs_user WITH PASSWORD '新密码';"
# 或开发环境清空重来（会丢数据）
docker compose down -v
```

### 只想用 Docker 跑数据库，后端在本地跑，可以吗？

可以，但基础编排没有把 PostgreSQL/Qdrant/Redis 端口发布到宿主机。加一个 override：

```yaml
# docker-compose.override.yml
services:
  postgres:
    ports: ["5432:5432"]
  qdrant:
    ports: ["6333:6333", "6334:6334"]
  redis:
    ports: ["6379:6379"]
```

然后把 `backend/.env` 指向 `localhost`。仅建议开发环境这样做，生产不要暴露三件套端口。

### Windows 上 Docker 起不来

先 `wsl --update`，确认 BIOS 虚拟化与 WSL2 已启用，再重启 Docker Desktop。安装目录与数据盘不要放在中文路径下。

## 模型与向量

### 支持哪些大模型？

任何 OpenAI 兼容端点：OpenAI、DeepSeek、DashScope 兼容模式、自建 vLLM/Ollama（需兼容 Chat Completions）。配置 `LLM_MODEL_ID` / `LLM_API_KEY` / `LLM_BASE_URL`。

`ROUTER_MODEL` 是**路由专用模型**，默认 `deepseek-flash`。如果换供应商，务必同步改掉，否则路由请求会 404。

### Embedding 怎么选？

| 类型 | 适用 | 备注 |
|---|---|---|
| `dashscope` | 有 DashScope Key，中文效果稳定 | 默认 `text-embedding-v3` |
| `local` | 数据不出内网 | 需安装 sentence-transformers，首次加载会下载模型 |
| `tfidf` | 完全离线、只做关键词级检索 | 语义能力弱，仅适合演示 |

更换 embedding 模型会改变向量维度，必须删除旧 Qdrant collection 并重建索引，否则检索报错或恒为空。

### 没有外网能用吗？

对话与向量模型需要可达的推理端点（可内网自建）。除此之外，镜像构建、`npm ci`、`pip install` 需要能访问依赖源；完全离线部署需提前准备镜像与依赖缓存。

### 模型调用费用怎么控制？

- 路由用轻量模型（`ROUTER_MODEL`），业务响应用主模型；
- `ROUTER_MAX_INTENTS` 控制单条消息最多几个意图；
- `RAG_TOP_K` 控制召回条数；
- `CONTEXT_MAX_TOKENS` 控制上下文预算；
- `TRACE_SAMPLE_RATE` 降低链路写入量（不直接影响模型费用）。

## 知识库与记忆

### 上传文档后 status=failed

看 `documents.error_message`，常见原因：

- `RAG_KB_ROOT` / `RAG_DOC_ARCHIVE_ROOT` 目录不可写（Docker 下检查卷属主）；
- Qdrant 不可达；
- 文件超过 `UPLOAD_MAX_SIZE_MB`（默认 20 MB），或反向代理 `client_max_body_size` 太小；
- 文件解析依赖缺失（PDF/Word 解析在镜像内已包含）。

### 检索结果恒空 / `chunk_count` 恒为 0

依次确认：文档状态是 `indexed`、查询的 namespace 与文档一致、Qdrant collection 有向量、`RAG_MIN_SCORE` 没有设得过高。切换过 embedding 模型时需重建索引。

### 记忆页是空的

- 首轮对话后才有 working 记忆，属正常；
- 确认 `MEMORY_TYPES` 包含需要的类型；
- staff 只能按用户查看记忆，客户只能看自己的；
- `WORKING_MEMORY_TTL` 过期后 working 记忆会消失。

### 启用 semantic 记忆后启动失败

`semantic` 硬依赖 Neo4j。未部署 Neo4j 时保持 `MEMORY_TYPES=working,episodic,perceptual`；部署后填好 `NEO4J_*` 再加入 `semantic`。

### 删除文档会清向量吗？

会。删除流程是先清 Qdrant 向量，再删归档与数据库记录。`memories` / `documents` 表只是归档镜像，运行时以 Qdrant 为准。

## MCP 与工具

### MCP 工具数为 0 / Agent 没有 MCP 工具

- `stdio` 方式由容器内 Python 拉起子进程，不需要端口；确认 `tools` 表里的 `config` 是 `transport=stdio`；
- Windows 下常见于 GBK 控制台：hello-agents 打印 emoji 抛 `UnicodeEncodeError` 被静默吞掉。`app/main.py` 已把 stdout/stderr 切到 UTF-8；手动跑脚本时先 `$env:PYTHONIOENCODING="utf-8"`；
- 用后台「MCP 服务管理」的连通性测试先验证。

### `npx` 类 MCP 示例启动失败

`npx` 需要联网下载包，离线环境会失败。仓库内置的物流/退款 MCP 是本地 Python 服务，不依赖 `npx`；外部示例建议单独 profile，确认网络后再启用。

### 工具"调了却没落库"或模型自演结果

自定义工具必须是 `async run(**kwargs)`，由 orchestrator 回投主事件循环执行。若看到模型输出伪造的 `[TOOL_CALL...]`，检查工具是否真的注册成功、是否被禁用，以及链路里有没有 `tool` span。

## 鉴权与反向代理

### 后台登录和客户登录为什么不通？

后台人员与客户是两张表、两个入口：

- 后台：`POST /api/v1/auth/staff/login`（`staff_users`，admin/agent）
- 客户：`POST /api/v1/auth/customer/login`（`customer_users`，可自助注册）

客户账号不能登录后台，后台账号也不能登录客户端的客户入口。

### 401 很频繁

- 客户端与管理端的 Token key 不同，不能混用；
- Access Token 默认 2 小时，过期后由前端单飞刷新；刷新失败会跳登录；
- 服务器与客户端时钟偏差过大会导致 JWT 立即过期，注意校时。

### SSE 不是逐字输出，而是一次性整段

某一层反向代理开启了缓冲。仓库自带 `docker/nginx.conf` 已关闭 `proxy_buffering`；外层再加 Nginx/Traefik 时也必须关闭，并把读超时设到 ≥300s。

### 反向代理后限流/IP 记录不对

只有直连对端命中 `TRUSTED_PROXIES` 时才采信 `X-Forwarded-For`。compose 默认只信任 web 容器静态 IP `172.28.0.10/32`；不要改成 `0.0.0.0/0` 或过大的网段。用 Caddy 时它会丢弃客户端伪造的 XFF；若在 Caddy 前再加云 LB，需要按真实链路重新配置。

## 运营台

### 新建的动态 Agent 总路由不到

看「路由分发器」页的候选清单：

- 不在清单里：Agent 被禁用或未启用；
- 在清单里但线索完整度是「仅描述」：没填 `routing_keywords`，提示词里只有一行描述；
- 填了关键词仍不命中：关键词超出前 12 个不会注入，或说法与客户真实表达不贴合。补 `routing_examples`（典型问法）通常更有效。

### 固定话术不生效 / 误吞正常问题

- 运行时只读 `canned_scripts` 表，代码里没有兜底规则；表为空或全部停用等于关闭该能力；
- 误吞只可能来自 `keywords`（包含即命中）：删掉过宽的词，用「命中测试」先验证；
- `patterns` 是整句归一化后全等，不会吞掉带后半句的复合提问。

### 链路追踪看不到某一轮

- 确认 `TRACE_ENABLED=true` 且 `request_traces` 表存在；
- `TRACE_SAMPLE_RATE < 1` 时正常应答会被采样丢弃，转人工/异常/工具报错轮始终保留；
- 固定话术命中的轮次没有 `route` 事件，只有 `canned.script` span，属正常。

## 数据与升级

### 怎么彻底重置数据？

```bash
docker compose down -v     # 删除命名卷，不可恢复
docker compose up -d --build
```

生产使用 bind mount，需停栈后清空 `DATA_ROOT` 下对应目录（先备份）。

### 升级后表结构不一致 / 缺列

当前没有 Alembic 版本化迁移。升级步骤：

```bash
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$COMPOSE run --rm backend python ../scripts/init_db.py   # 幂等建表 + 增量列
```

开发环境可直接全量重建（会清空数据）：`python ../scripts/rebuild_and_seed.py`。

### 怎么迁移到另一台机器？

1. 在旧机器执行 `bash scripts/backup.sh`；
2. 新机器部署同版本，初始化到空库；
3. 按 [部署指南](deployment.md) §6.2 恢复 PostgreSQL、Qdrant、业务数据与 Redis；
4. 修正 `DATA_ROOT` 属主为 `1000:1000`，重启并验证。

## 其他

### 可以商用吗？

核心采用 Apache-2.0，可商用与二次开发，需保留许可证与版权声明。企业版能力（SSO、RBAC、审计增强等）尚未提供。

### 支持范围？

当前是单实例单租户、社区支持（issue 尽力响应）。生产上线前请自行完成备份演练与安全评估。
