# 配置说明

> 本文以 `backend/app/core/config.py` 与 `backend/.env.example` 为准，列出全部可用变量、默认值与生效方式。
> 首次部署只需看「最小必填配置」，其余按需调整。

## 1. 三份配置来源

| 来源 | 谁在读 | 用途 |
|---|---|---|
| 仓库根 `.env` | `docker compose` | Compose 变量插值与 backend 的 `env_file`；**Docker 部署只需要这一份** |
| `backend/.env` | 后端进程（pydantic-settings + `load_dotenv`） | 本地开发（不用 Docker）时的配置 |
| 进程环境变量 | Compose / 后端 | 优先级最高，可用于临时覆盖 |

优先级（从高到低）：

1. Shell 中已导出的环境变量；
2. Compose 的 `environment:` 段（容器内固定覆盖，如 `DATABASE_URL`、`QDRANT_URL`）；
3. 根 `.env`（Compose 插值）或 `backend/.env`（本地运行）。

> 注意：`load_dotenv` 默认**不覆盖**已存在的环境变量。排查"改了 `.env` 不生效"时，先 `echo $env:DATABASE_URL`（PowerShell）确认真实值。

## 2. 最小必填配置

Docker Compose 部署时，根 `.env` 至少要有：

```ini
LLM_MODEL_ID=...
LLM_API_KEY=...
LLM_BASE_URL=...

EMBED_MODEL_TYPE=dashscope
EMBED_MODEL_NAME=text-embedding-v2
EMBED_API_KEY=...
EMBED_BASE_URL=...

JWT_SECRET=<≥32 字节随机串>
```

`JWT_SECRET` 由 Compose 强制校验（`${JWT_SECRET:?}`），未设置会直接拒绝启动；`APP_ENV=production` 时后端还会校验其长度与常见弱值。

## 3. 全量变量

### 3.1 应用与 CORS

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_ENV` | `development` | 设为 `production` / `prod` 时启用生产校验（JWT 强度等） |
| `APP_NAME` | `smart-cs` | 应用名，出现在 OpenAPI 信息中 |
| `APP_HOST` | `0.0.0.0` | 监听地址（uvicorn 由启动命令决定，通常不用改） |
| `APP_PORT` | `8000` | 容器内端口；宿主机端口用 `BACKEND_PORT` |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174` | CORS 白名单，逗号分隔。单镜像部署前后端同源，无需额外放行 8080；嵌入场景还会叠加各自 `scenes.allowed_origins`（后台配置，保存后立即生效） |

### 3.2 种子数据

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEED_DEMO` | `false` | `true` 时写入演示账号、示例文档与演示动态 Agent，**仅演示环境** |
| `SEED_ADMIN_USERNAME` | `admin` | 非演示模式创建的初始管理员用户名 |
| `SEED_ADMIN_PASSWORD` | 空 | 留空则随机生成并只打印一次；显式指定时要求足够强度 |

种子脚本是幂等的，已存在的账号不会被改密；需要重置密码用 `python -m app.setup --reset-password`（见 [FAQ](faq.md#管理员密码丢了怎么办)）。

### 3.3 对话模型（HelloAgents 直接读取）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_MODEL_ID` | `gpt-4o` | 对话模型名，需与 `LLM_BASE_URL` 的提供方匹配 |
| `LLM_API_KEY` | `sk-xxx` | 模型 Key |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容端点 |
| `LLM_TIMEOUT` | `60` | 单次请求超时（秒） |
| `LLM_MAX_RETRIES` | `2` | 失败重试次数 |

### 3.4 向量模型（记忆与 RAG 共用）

| 变量 | 默认 | 说明 |
|---|---|---|
| `EMBED_MODEL_TYPE` | `dashscope` | `dashscope` / `local` / `tfidf`；`local` 需额外安装 sentence-transformers，`tfidf` 无需外部服务但语义能力弱 |
| `EMBED_MODEL_NAME` | 空 | `dashscope` 默认 `text-embedding-v3`；`local` 默认 `sentence-transformers/all-MiniLM-L6-v2` |
| `EMBED_API_KEY` | 空 | 向量服务 Key |
| `EMBED_BASE_URL` | 空 | **不要**以 `/embeddings` 结尾，框架会自动追加 |

> 更换 embedding 模型会改变向量维度，旧向量不可复用：需要删除对应 Qdrant collection 并重建索引，否则检索报错或恒为空。

### 3.5 RAG 与知识库

| 变量 | 默认 | 说明 |
|---|---|---|
| `RAG_KB_ROOT` | `./data/knowledge_bases` | 知识库根目录（Compose 中覆盖为 `/app/backend/data/knowledge_bases`） |
| `RAG_DOC_ARCHIVE_ROOT` | `./data/doc_archives` | 上传原文归档目录，供后台下载（Compose 中为 `/app/backend/data/doc_archives`） |
| `RAG_DEFAULT_NAMESPACE` | `cs_default` | 未指定 namespace 时的默认知识库 |
| `UPLOAD_MAX_SIZE_MB` | `20` | 单文档上传上限；需与 Nginx `client_max_body_size 25m` 配套 |
| `RAG_CHUNK_SIZE` | `512` | 分块大小（字符） |
| `RAG_CHUNK_OVERLAP` | `64` | 分块重叠 |
| `RAG_TOP_K` | `5` | 默认召回条数 |
| `RAG_MIN_SCORE` | `0.3` | 召回最低相似度 |

### 3.6 数据库 / Redis / Qdrant

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://cs_user:cs_pass@localhost:5432/cs_db` | PostgreSQL（asyncpg 驱动） |
| `DB_POOL_SIZE` | `20` | 连接池大小 |
| `DB_MAX_OVERFLOW` | `10` | 池溢出上限 |
| `REDIS_URL` | `redis://localhost:6379/0` | 工作记忆/缓存；生产带密码写法见 3.12 |
| `WORKING_MEMORY_TTL` | `1800` | working 记忆过期秒数 |
| `QDRANT_URL` | 空（回退 `http://localhost:6333`） | 向量库地址 |
| `QDRANT_API_KEY` | 空 | Qdrant API Key（生产叠加强制非空） |
| `QDRANT_COLLECTION` | `hello_agents_vectors` | 向量集合名，记忆与 RAG 共用 |
| `QDRANT_DISTANCE` | `cosine` | 距离度量（框架读取） |

> Docker 部署时 `DATABASE_URL` / `REDIS_URL` / `QDRANT_URL` 会被 Compose 的 `environment:` 覆盖为容器服务名，根 `.env` 里写 `localhost` 也不会影响容器内连接。

### 3.7 记忆与 Neo4j

| 变量 | 默认 | 说明 |
|---|---|---|
| `MEMORY_TYPES` | `working,episodic,perceptual` | 逗号分隔；加入 `semantic` 前必须先部署 Neo4j |
| `MEMORY_IMPORTANCE_THRESHOLD` | `0.35` | 记忆重要性阈值（`.env.example` 示例值为 `0.6`，以实际配置为准） |
| `MEMORY_ACCESS_THRESHOLD` | `3` | 访问次数达到该值才参与晋升 |
| `MEMORY_DECAY_HALF_LIFE_DAYS` | `30` | 衰减半衰期（天） |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | 空 | 仅启用 `semantic` 时需要 |

### 3.8 鉴权

| 变量 | 默认 | 说明 |
|---|---|---|
| `JWT_SECRET` | `change_me_in_prod` | **生产必须改为 ≥32 字节随机串**；生产环境弱密钥会导致启动失败 |
| `JWT_ALGORITHM` | `HS256` | 签名算法 |
| `ACCESS_TOKEN_TTL` | `7200` | Access Token 有效期（秒） |
| `REFRESH_TOKEN_TTL` | `604800` | Refresh Token 有效期（秒） |
| `TRUSTED_PROXIES` | 空 | 逗号分隔 IP/CIDR；仅当直连对端命中时才采信 `X-Forwarded-For`。Compose 默认 `172.28.0.10/32`（web 容器静态 IP） |

### 3.9 路由

| 变量 | 默认 | 说明 |
|---|---|---|
| `ROUTER_MODEL` | `deepseek-flash` | 路由专用轻量模型，**必须与你的模型服务匹配** |
| `ROUTER_CONFIDENCE_THRESHOLD` | `0.5` | 主意图低于该值整轮转人工 |
| `ROUTER_MAX_INTENTS` | `2` | 单条消息最多执行的意图数；`1` 即关闭多意图 |
| `ROUTER_FALLBACK_ANSWER` | `true` | 路由判定"不贴合任何 Agent"时是否先由兜底 Agent 应答；`false` 则直接转人工 |

路由分发器本身不入库、不可在线修改，只读视图见运营台「路由分发器」。

### 3.10 公开接入（嵌入 / API 对接）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PUBLIC_CHAT_RATE_LIMIT_PER_MINUTE` | `30` | 每个 `scene + user/visitor/ip` 每分钟对话次数；`0` 关闭该项 |
| `PUBLIC_CHAT_DAILY_QUOTA` | `2000` | 每个 `scene + 主体` 每日配额；`0` 关闭 |
| `PUBLIC_CHAT_MAX_CONCURRENT` | `3` | 同一主体同时未结束的 SSE 流上限；`0` 不限制 |
| `SCENE_ORIGIN_CACHE_TTL` | `60` | 场景 `allowed_origins` 的进程内缓存秒数；后台保存来源会主动失效 |

限流依赖 Redis；Redis 不可用时 fail-open（放行并每分钟告警一次）。公开接口与鉴权说明见 [API 速查](api.md#public无需登录嵌入--api-对接)。

### 3.11 上下文预算

| 变量 | 默认 | 说明 |
|---|---|---|
| `CONTEXT_MAX_TOKENS` | `8000` | 单轮上下文总预算 |
| `CONTEXT_SYSTEM_RESERVE` | `800` | 系统提示预留 |
| `CONTEXT_HISTORY_RESERVE` | `2000` | 历史消息预留 |

### 3.12 链路追踪

| 变量 | 默认 | 说明 |
|---|---|---|
| `TRACE_ENABLED` | `true` | 是否记录每轮链路 |
| `TRACE_STORE_PAYLOAD` | `true` | 是否保存工具入参/返回（含客户数据，截断保存）；合规敏感场景设 `false` |
| `TRACE_PAYLOAD_MAX_CHARS` | `2000` | payload 截断长度 |
| `TRACE_SAMPLE_RATE` | `1.0` | 正常应答采样率；转人工与异常轮始终全量记录 |
| `TRACE_RETENTION_DAYS` | `30` | 保留天数，供 `scripts/cleanup_traces.py` 使用 |

### 3.13 Docker Compose 与生产叠加

| 变量 | 默认 | 说明 |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_DB` | `cs_user` / `cs_db` | 数据库账号与库名 |
| `POSTGRES_PASSWORD` | `cs_pass` | **仅首次创建数据卷时生效**；已有卷改密码需进库 `ALTER USER` |
| `BACKEND_PORT` | `8000` | 后端映射到宿主机回环的端口 |
| `WEB_PORT` | `8080` | Web 端口（生产叠加中不再发布） |
| `INIT_ON_START` | `true` | 启动时是否执行 `init_db` / `seed`；生产设为 `false`，用一次性命令初始化 |
| `DOMAIN` | 无（生产必填） | Caddy 自动 HTTPS 使用的域名 |
| `DATA_ROOT` | `/srv/smart-cs` | 生产叠加的 bind mount 根目录 |
| `REDIS_PASSWORD` | 无（生产必填） | Redis `requirepass`；含 `@ : /` 等字符需 URL 编码，建议字母数字 |
| `QDRANT_API_KEY` | 无（生产必填） | Qdrant 服务端鉴权与客户端使用同一值 |

生产叠加下 backend 会自动使用：

```ini
DATABASE_URL=postgresql+asyncpg://<user>:<password>@postgres:5432/<db>
REDIS_URL=redis://:<password>@redis:6379/0
```

## 4. 典型组合

**本地开发（`backend/.env`）**

```ini
DATABASE_URL=postgresql+asyncpg://cs_user:cs_pass@localhost:5432/cs_db
REDIS_URL=redis://localhost:6379/0
QDRANT_URL=http://localhost:6333
RAG_KB_ROOT=./data/knowledge_bases
```

**单机 Docker（根 `.env`）**

```ini
JWT_SECRET=<随机>
POSTGRES_PASSWORD=<强密码>
WEB_PORT=8080
SEED_DEMO=false
# DATABASE_URL / REDIS_URL / QDRANT_URL 由 compose 覆盖，无需填写
```

**生产叠加（根 `.env`）**

```ini
APP_ENV=production
JWT_SECRET=<随机>
POSTGRES_PASSWORD=<强密码>
REDIS_PASSWORD=<字母数字随机>
QDRANT_API_KEY=<强随机>
DOMAIN=cs.example.com
DATA_ROOT=/srv/smart-cs
INIT_ON_START=false
```

## 5. 修改后如何生效

| 改动 | 生效方式 |
|---|---|
| 根 `.env`（Docker） | `docker compose up -d`（必要时加 `--force-recreate backend`）；仅初始化时读取的项见下 |
| `backend/.env`（本地） | 重启后端进程 |
| `POSTGRES_PASSWORD` / `POSTGRES_USER` / `POSTGRES_DB` | 只在数据卷首次初始化时生效；已有库需进库修改 |
| `SEED_DEMO` | 只在种子脚本运行时生效；后端每次启动都会跑种子（`INIT_ON_START=true`），改完重建 backend 即可补数据 |
| 运营台里的 Agent / 知识库 / 工具配置 | 即时生效，无需重启 |
| `ROUTER_*` / `CONTEXT_*` / `TRACE_*` 等 | 进程启动时读取，需重启 backend |
| 更换 embedding 模型 | 需重建 Qdrant 向量（见 3.4） |

## 6. 安全相关

- `JWT_SECRET`、`POSTGRES_PASSWORD`、`REDIS_PASSWORD`、`QDRANT_API_KEY` 不要提交到仓库；根 `.env` 已被 `.gitignore` 排除。
- 对外环境关闭 `TRACE_STORE_PAYLOAD`，或确认链路数据的访问权限与保留期符合合规要求。
- `ALLOWED_ORIGINS` 生产改为实际域名白名单；嵌入场景还需在后台「场景 → 嵌入配置」维护 `allowed_origins`。
- 公开聊天限流依赖 Redis，多实例部署共享同一计数；Redis 故障时自动放行，需在监控中关注告警。
- 上传上限 `UPLOAD_MAX_SIZE_MB` 与 Nginx `client_max_body_size` 必须同步调整。
- `/metrics` 不在 Nginx 对外暴露；需要指标时用带鉴权的 `/api/v1/admin/metrics`。
