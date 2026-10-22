# 快速开始

> 目标：从一台只装了 Docker 的机器出发，跑通「启动 → 登录 → 第一轮对话 → 看到链路」。
> 预计耗时：首次构建 5–10 分钟（取决于网络），之后启动约 1 分钟。
> 需要更细的配置说明见 [配置说明](configuration.md)，生产上线见 [部署指南](deployment.md)。

## 1. 前置条件

| 项 | 要求 |
|---|---|
| Docker | Docker Engine 或 Docker Desktop（Windows/macOS） |
| Compose | **v2.24.4+**（生产叠加用到了 `!reset`，低版本会解析失败） |
| 资源 | 建议 4 核 8 GB 以上；镜像含文档解析依赖，后端镜像约 1.5 GB |
| 网络 | 能访问模型服务（OpenAI 兼容端点）与 Docker Hub；国内拉镜像可自行配置镜像加速 |
| 端口 | 默认占用 `8080`（Web）与 `127.0.0.1:8000`（后端调试）；冲突时见第 6 节 |

检查版本：

```bash
docker --version
docker compose version
```

## 2. 准备配置文件

**关键区别**：Compose 读取的是**仓库根目录**的 `.env`；`backend/.env` 只用于本地开发（不用 Docker 时）。

```bash
cp backend/.env.example .env        # Windows PowerShell: copy backend\.env.example .env
```

打开根 `.env`，至少填好三组：

```ini
# 1) 对话模型：任意 OpenAI 兼容端点
LLM_MODEL_ID=deepseek-chat
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1

# 2) 向量模型：记忆与 RAG 都依赖它
EMBED_MODEL_TYPE=dashscope
EMBED_MODEL_NAME=text-embedding-v2
EMBED_API_KEY=sk-xxx
EMBED_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 3) 鉴权：未设置或过短会拒绝启动
JWT_SECRET=<openssl rand -base64 48 的输出>
```

生成随机密钥：

```bash
openssl rand -base64 48
```

> Windows 没有 openssl 时，可用 Git Bash 执行上面的命令，或安装 OpenSSL 后执行。
> **不要**把示例值 `change_me_in_prod` 带到任何对外环境。

`ROUTER_MODEL` 默认是 `deepseek-flash`，必须与你的模型服务匹配。如果你用 DeepSeek，改成实际模型名；如果用的是 OpenAI/其他厂商，也一并改掉，否则路由会失败。

其余变量可以先用默认值，含义见 [配置说明](configuration.md)。

## 3. 启动

```bash
docker compose up -d --build
docker compose ps
```

期望结果：`postgres`、`qdrant`、`redis`、`backend`、`web` 五个服务全部 `healthy`。首次启动 backend 会依次执行建表与种子（幂等），日志中能看到 `[自检] 数据库 / Redis / Qdrant / 模型` 四行。

如果 `backend` 一直 `starting` 或退出，先看日志：

```bash
docker compose logs -f backend
```

## 4. 拿到管理员账号

默认 `SEED_DEMO=false`：只创建一个管理员，密码随机生成并**只在首次执行种子时打印一次**。

```bash
docker compose logs backend | grep -i "密码"
# Windows PowerShell: docker compose logs backend | Select-String "密码"
```

如果错过了当时的输出：`docker compose logs backend` 是累积日志，首次创建的那段通常还在，可以从头查找；确定已丢失时，可用初始化向导重置（见 [FAQ「管理员密码丢了怎么办」](faq.md#管理员密码丢了怎么办)）。仅本地演示环境也可以 `docker compose down -v` 后重新启动。

> 想在本地看演示数据，在**首次启动前**把 `SEED_DEMO=true` 写入根 `.env`。演示账号为 `admin / Admin@123`、`service01 / Agent@123`、`alice / Alice@123`；同时会创建一个 `customer_service` 客服场景（LLM 路由到投诉处理 / 通用知识问答 / 订单服务三个 Agent）和默认接入方（公开标识 `ic_default`，已授权全部场景；单租户嵌入无需传 `data-integration`）。仅用于演示，生产必须关闭。

## 5. 第一次验证

1. 打开管理后台 http://localhost:8080/admin/ ，用管理员登录；
2. 打开客户对话端 http://localhost:8080/ ，注册一个客户账号（或使用演示账号 `alice`），发一句业务问题，例如：
   - `帮我查一下订单 SO20240001 的物流`
   - `我要退款，订单 SO20240002`
   - `你们的知识库里有退货政策吗`
3. 回复应当是**逐字增量出现**的（SSE 流式），不是一次性整段；
4. 回到管理后台「链路追踪」，找到刚才这一轮，点开应能看到 `route` / `llm` / `tool` / `rag` 等 spans 与耗时；
5. 「咨询管理」里能看到这次会话，「记忆管理」里能看到本轮写入的记忆（首轮可能为空，属正常）。

> 固定话术（如"你好""你是谁"）在路由之前就会命中，这类轮次只有 `canned.script` span，没有 `route` 事件，属预期行为。

## 6. 常见启动问题

| 现象 | 处理 |
|---|---|
| `port is already allocated`（8080/8000） | 在根 `.env` 改 `WEB_PORT` / `BACKEND_PORT`，再 `docker compose up -d` |
| `JWT_SECRET` 相关报错 | 根 `.env` 没设置或值太短；生产要求 ≥32 字节随机串 |
| backend 反复重启，日志提示 Qdrant 不可用 | `docker compose ps` 看 `qdrant` 是否 healthy；Qdrant 是记忆/RAG 硬依赖，起不来 backend 会主动退出 |
| `web` 不健康，`/admin/` 502 | 先确认 backend healthy；Nginx 通过 Docker DNS 动态解析 backend，通常重建 backend 后自动恢复 |
| 登录提示账号不存在 | 后台与客户是两套账号：后台走 `/auth/staff/login`，客户走 `/auth/customer/login`，客户可自助注册 |
| 构建卡在 `npm ci` / `pip install` | 网络问题；配置 Docker 镜像加速或代理后重试 |
| Windows 上 Docker 启动失败 | 先 `wsl --update`，确认 BIOS 虚拟化与 WSL2 已启用 |

更多排障见 [常见问题](faq.md) 与 [运维手册](runbook.md)。

## 7. 停止与清理

```bash
docker compose stop              # 停止，保留数据
docker compose down              # 删除容器，保留命名卷
docker compose down -v           # 连数据卷一起删除（不可恢复，谨慎）
```

数据卷：`pgdata`、`qdrantdata`、`redisdata`、`backenddata`（知识库与文档归档）、`memorydata`（记忆持久化）。

## 8. 下一步

- 配置每一项的含义：[配置说明](configuration.md)
- 上生产（HTTPS、固定数据目录、备份）：[部署指南](deployment.md)
- 日常运维与排障：[运维手册](runbook.md)
- 想做二次开发：先读 [CONTRIBUTING.md](../CONTRIBUTING.md) 与 [开发规范](spec.md)
