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

### 上传文档报 500，日志里 Qdrant 报 502 Bad Gateway

Windows 开了 Clash 一类系统代理时，httpx 会把 `http://127.0.0.1:6333` 也交给代理，代理对回环地址返回 502，RAG 工具初始化失败，上传接口随之 500。应用启动时会自动把 `localhost` / `127.0.0.1` / `::1` 并入 `no_proxy`；自己写的脚本要直连本机服务，同样需要 `NO_PROXY`（或 `httpx.Client(trust_env=False)`）。

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

### IT 资产查询查不到数据

- 演示资产是进程内只读快照，重启即恢复初始数据；支持资产编号、SN、领用人、型号、IP、MAC 与“电脑/笔记本/台式机”等品类别名；
- 生产接入请替换 `app/tools/custom/mock_assets.py`，改为真实 CMDB/资产系统查询；
- 查不到时工具返回 `ok=false`，可在链路追踪的 tool span 里看到调用参数与结果。

### 链路追踪看不到某一轮

- 确认 `TRACE_ENABLED=true` 且 `request_traces` 表存在；
- `TRACE_SAMPLE_RATE < 1` 时正常应答会被采样丢弃，转人工/异常/工具报错轮始终保留；
- 固定话术命中的轮次没有 `route` 事件，只有 `canned.script` span，属正常。

### 原来的「用量报表」页去哪了 / 后台为什么看不到 token

- 请求数与每日趋势已并入「数据分析」（`/analytics`）：全量三率 + 每日请求量来自 `chat_usage_daily`（无采样偏差、可对账），链路三率来自 `request_traces`；
- token 是框架估算值（`hello_agents` 不回传供应商真实 usage），口径上不能当账单，因此接口与页面都不输出；库内 `chat_events.total_tokens` / `chat_usage_daily.tokens` 仍照写，以后接入真实 usage 可复用；
- `/admin/usage/*`（含 CSV 导出）已下线，需要日维度数据直接调 `GET /admin/analytics/outcomes`，取 `daily` 字段（`day / requests / handoffs / errors`，只含当天有请求的日期、不补零）。

### 「问题解决率」为什么不是满意度

解决率 = `(请求数 − 转人工 − 错误) / 请求数`，衡量的是**机器人自主把这一轮结束掉的比例**，不是用户评价；
要满意度得接入评分或人工标注。分母为 0 时接口返回 `null`、页面显示「—」，不伪造成 0%。

## 数据与升级

### 怎么彻底重置数据？

```bash
docker compose down -v     # 删除命名卷，不可恢复
docker compose up -d --build
```

生产使用 bind mount，需停栈后清空 `DATA_ROOT` 下对应目录（先备份）。

### 升级后表结构不一致 / 缺列

表结构由自研的版本化增量迁移推进（不依赖 Alembic）：`scripts/migrations/NNNN_*.sql` + `schema_migrations` 记录表，
升级后执行：

```bash
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$COMPOSE run --rm backend python -m app.migrate --check   # 看当前版本与待执行迁移（有待执行时退出码 1）
$COMPOSE run --rm backend python -m app.migrate           # 按编号顺序执行到当前 SCHEMA_VERSION
```

- 空库不用迁移：`init_db.sql` 一次建到最新结构（当前 `SCHEMA_VERSION=10`）；
- `python ../scripts/init_db.py` 只建表、**不记录已执行版本**，存量库不要用它代替迁移；
- 迁移只向前、不回滚，跨版本回退要 `pg_restore` 到升级前备份（见 [部署 §5](deployment.md)）；
- 开发/演示可直接全量重建（会清空数据）：`python ../scripts/rebuild_and_seed.py`。

### 怎么迁移到另一台机器？

1. 在旧机器执行 `bash scripts/backup.sh`；
2. 新机器部署同版本，初始化到空库；
3. 按 [部署指南](deployment.md) §6.2 恢复 PostgreSQL、Qdrant、业务数据与 Redis；
4. 修正 `DATA_ROOT` 属主为 `1000:1000`，重启并验证。

## 其他

### 可以商用吗？

核心采用 Apache-2.0，可商用与二次开发，需保留许可证与版权声明。企业版功能（审计查询、白标、评测管理、SSO，
以及细分到场景/知识库/工单的资源级授权）以私有包/镜像交付，需单独的实例年授权
（授权文件只降级不阻断，见 [配置说明 3.15](configuration.md)）。
后台「角色与权限」的**菜单模块级**读写矩阵已在开源版提供（`app/core/rbac.py`，见 [U26](updates/26-rbac-menu-matrix.md)），不占企业功能位。

## 授权与版本

### 后台显示“社区版”，但我已经放了 license.json？

看后台「授权与版本」页的“校验结论”，按原因处理：

| 结论 | 处理 |
|---|---|
| 未找到授权文件 | `LICENSE_FILE` 路径不对（容器里要写容器内路径，如 `/app/backend/license.json`），或文件没挂载进去 |
| 未配置 `LICENSE_PUBLIC_KEY` | 把授权方给的公钥填进 `.env`，重启或点“重新加载” |
| 签名校验失败 | 文件被改过（哪怕只改了 `features`）或不是同一家签发的，重新要一份原件 |
| 绑定的是另一个 `DEPLOYMENT_ID` | 授权按实例签发：把本机 `.env` 的 `DEPLOYMENT_ID` 改成授权里的值，或让对方按本机指纹重签 |
| 已到期 | 续期后覆盖文件，点“重新加载”即恢复，不需重启 |

降级后对话、知识库、工具/MCP、记忆、追踪、数据分析全部照常，只有 5 个企业功能位关闭。

### `license.json` 要提交到 Git 吗？

不要。授权文件含客户名与合同号，已加入 `.gitignore`；每个部署单独保存，换机器需重新签发或改 `DEPLOYMENT_ID`。

### 支持范围？

当前是单实例单租户、社区支持（issue 尽力响应）。生产上线前请自行完成备份演练与安全评估。

## 评测与效果

> 完整口径见 [评测与回归](evaluation.md)；发布前的固定动作见 [运维手册 6.1](runbook.md)。

### 怎么证明改了提示词 / 换了模型没有变差？

跑一次带基线对比的评测，看退出码：

```powershell
cd backend
.\.venv\Scripts\python.exe ..\scripts\eval.py --scene <你的场景> --baseline latest --run-tag <版本或变更>
echo $LASTEXITCODE
```

退出码 `0` 全通过 / `1` 有失败但无退化 / **`2` 相对基线退化（发布闸门）** / `3` 链路根本没跑通。
基线是 `eval_runs` 里**同一用例集指纹 + 同一场景**的最近一次；换模型或改了路由/RAG 阈值等于换基线，
第一次不带 `--baseline` 重建基线并在变更说明里写清。

### 报告里 RAG 命中率低，是产品不行吗？

不一定。先区分三件事（处置完全不同，详细看 runbook 第 5 节）：

1. **库绑少了** —— 业务 Agent 的 `rag_namespaces` 没包含政策所在的那个库（看失败用例的 `rag_namespaces` 与检回的命名空间）；
2. **索引脏** —— 重复跑 seed 不清 Qdrant，会留下同一块的多个重复点，分数噪声大、token 也虚高；
3. **文档粒度过粗** —— 一篇长文档整篇一块时，窄问法的相似度会落在 `RAG_MIN_SCORE` 之下检回为空。
   这时正确做法是**把 FAQ 拆成一条一题**，而不是降阈值或改用例期望 —— 后者会让报告失去意义。

### 跑评测要准备什么？

服务已启动且 `/health` 可达（注意它挂在根路径，不带 `/api/v1`）；场景开放匿名时用 publish key 即可，
未开放匿名需 `--customer/--password` 或 `--api-key`；验收跑建议临时 `TRACE_SAMPLE_RATE=1.0`（否则 RAG 指标因采样落到不可观测）；
`--concurrency` 默认 2，拉高容易撞模型限流并把结果打花。报告写在 `reports/`（不入库），运行结论进 `eval_runs`。

### 用例集可以自己写吗？改了会不会把报告改好看？

可以写（`backend/tests/fixtures/eval/<scene_code>.json`），但**评审后才可改**。诚实说：`expect` 全部可选，
少写期望确实会让该用例更容易 passed（分项指标只会因此把分母缩小、显示成 `—`，不会抬高），
所以约束不靠口径而靠三件事：单测 `test_repo_cases_each_expect_something` 保证每条用例至少有一个检查点；
报告第 3 节把每个指标的分子/分母/不可观测三个数都摊开；用例集本身进 Git、改动需评审。
改判定口径必须在该用例 `note` 里写清代码位置或政策条目（首批只改过两处，见 [U24 §5.4](updates/24-eval-regression.md)）。
改完先跑 `..\scripts\eval.py --dry-run` 验证结构；改了判定字段会使指纹变化，与旧基线的 diff 会被标为「不可比」。
