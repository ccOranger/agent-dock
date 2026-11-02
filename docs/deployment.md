# 部署指南

> 面向单机 / 小规模私有化部署。日常排障见 [运维手册](runbook.md)，配置项含义见 [配置说明](configuration.md)。
> 当前版本为单实例单租户，不支持水平扩容与多租户隔离。

## 1. 选择部署形态

| 形态 | 适用场景 | 组成 |
|---|---|---|
| 本地开发 | 改代码、调提示词 | 本机 Python + Node + 外部 PostgreSQL/Redis/Qdrant |
| 单机 Docker Compose | 演示、内网试用 | `docker-compose.yml`：postgres / qdrant / redis / backend / web |
| 生产叠加 | 对外提供服务的单机 | 基础编排 + `docker-compose.prod.yml`：Caddy HTTPS、bind mount、资源限制、一次性初始化 |
| 已有反向代理 | 宿主机已跑 Nginx/Traefik | 基础编排 + 外层代理转发到 web，注意端口暴露与 XFF |
| 复用宿主机中间件 | 服务器已装 PostgreSQL/Redis/Qdrant/Nginx | `docker-compose.external.yml`：只跑 backend 一个容器，两端静态由宿主机 Nginx 托管（见 §4.2） |

## 2. 单机 Docker Compose（基础）

```bash
cp backend/.env.example .env
# 填 LLM_* / EMBED_* / JWT_SECRET，其余按需
docker compose up -d --build
docker compose ps
```

- 入口：`http://<host>:8080/`（客户）、`http://<host>:8080/admin/`（运营台）
- 后端只绑定宿主机回环 `127.0.0.1:8000`，不对外暴露
- 数据在命名卷：`pgdata`、`qdrantdata`、`redisdata`、`backenddata`、`memorydata`
- 首次启动自动执行 `init_db` + `seed`（幂等）；存量库升级需设 `MIGRATE_ON_START=true`，或在升级流程里显式执行 `python -m app.migrate`

## 3. 生产叠加

### 3.1 它解决什么

| 项 | 基础编排 | 生产叠加 |
|---|---|---|
| HTTPS | 无 | Caddy 自动申请/续期证书，SSE 不缓冲 |
| 数据位置 | Docker 命名卷 | `DATA_ROOT` 下的 bind mount，便于备份与迁移 |
| 数据库密码 | 默认值可用 | 强制非空，Redis 加 `requirepass`，Qdrant 加 API Key |
| 初始化 | 每次启动执行 | `INIT_ON_START=false`，首次部署用一次性命令 |
| 资源 | 不限制 | `cpus` / `mem_limit` + 日志轮转（20m × 5） |
| 端口 | 8080 对外 | 只暴露 80/443，web 不再发布端口 |

### 3.2 前置

- 域名已解析到本机（A/AAAA），80/443 可从公网访问（80 用于 ACME 挑战）；
- 宿主机没有其他程序占用 80/443；
- Compose ≥ **v2.24.4**（`!reset` 需要）；
- Linux 主机（生产叠加面向 Linux；Windows 本地验证需把 `DATA_ROOT` 指到可写盘符）。

根 `.env`：

```ini
APP_ENV=production
JWT_SECRET=<openssl rand -base64 48>
POSTGRES_PASSWORD=<强密码>
REDIS_PASSWORD=<字母数字随机串>
QDRANT_API_KEY=<强随机>
DOMAIN=cs.example.com
DATA_ROOT=/srv/smart-cs
INIT_ON_START=false
```

> `REDIS_PASSWORD` 含 `@ : /` 等字符需要 URL 编码，建议只用字母数字。
> `POSTGRES_PASSWORD` 对**已有数据卷**不生效，老库需进库 `ALTER USER` 或全新部署。

### 3.3 首次部署

```bash
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

$COMPOSE up -d --build postgres qdrant redis   # 先起依赖
$COMPOSE run --rm backend python -m app.migrate            # 空库：用 init_db.sql 建到最新结构
$COMPOSE run --rm backend python ../scripts/seed_data.py   # 管理员随机密码只打印一次
$COMPOSE up -d --build
$COMPOSE ps
```

`init-perms` 一次性容器会把 `backenddata` / `memorydata` 的属主改为 `1000:1000`（backend 以 `appuser` 运行），完成后退出属正常。

访问 `https://<DOMAIN>/` 与 `https://<DOMAIN>/admin/`。首次签发证书看：

```bash
$COMPOSE logs -f caddy
```

证书与配置持久化在命名卷 `caddydata` / `caddyconfig`，**不要** `docker compose down -v`。

> 已有基础栈切到生产叠加时：两者项目名与固定子网相同，不能同时运行；且生产用 bind mount，不会自动继承命名卷里的数据。要么全新初始化，要么停栈后把卷内容复制到 `DATA_ROOT`（PostgreSQL 需停库后复制，否则数据可能不一致）。

### 3.4 资源与日志

生产叠加默认限制：postgres 1 CPU / 1 GB，qdrant 2 CPU / 2 GB，redis 0.5 CPU / 512 MB，backend 2 CPU / 2 GB，web 0.5 CPU / 256 MB，caddy 0.5 CPU / 256 MB。按实际负载在 `docker-compose.prod.yml` 调整。

所有服务使用 `json-file` 日志驱动，单文件 20 MB、保留 5 个。

## 4. 已有反向代理（不用 Caddy）

### 4.1 中间件仍用编排内置，只把 web 挂到外层代理下

如果宿主机已有 Nginx/Traefik 并负责证书：

1. **不要直接暴露 8080**。基础编排的 `web.ports` 默认发布到所有网卡，绕过外层代理后可伪造 `X-Forwarded-For`。加一个 override 文件绑定回环：

   ```yaml
   # docker-compose.override.yml
   services:
     web:
       ports: !override
         - "127.0.0.1:${WEB_PORT:-8080}:80"
   ```

2. 外层代理转发到 `http://127.0.0.1:8080`，需保留 SSE 配置（关闭缓冲、放大读超时）与上传大小：

   ```nginx
   server {
       listen 443 ssl http2;
       server_name cs.example.com;

       client_max_body_size 25m;

       location / {
           proxy_pass http://127.0.0.1:8080;
           proxy_http_version 1.1;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_buffering off;
           proxy_read_timeout 600s;
       }
   }
   ```

3. `TRUSTED_PROXIES` 保持默认 `172.28.0.10/32`（只采信 web 容器）。web 的 Nginx 会把外层代理传来的客户端 IP 继续向左追加，backend 取最左值；前提是 8080 没有对外暴露。

4. 云负载均衡再加一层时，需按实际链路重新确认 `TRUSTED_PROXIES` 与各层 XFF 追加规则，不能照抄。

### 4.2 连中间件都不起容器：复用宿主机的 PostgreSQL / Redis / Qdrant + Nginx

适用于服务器已自行安装并运维这四个服务的形态。用 `docker-compose.external.yml`（**自包含**，只定义 backend）：

> 不要把两个文件叠在一起用：`-f docker-compose.yml -f docker-compose.external.yml` 会把 postgres/qdrant/redis
> 容器重新带起来，变成「容器里一套数据、宿主机上另一套数据」。正确写法始终只带一个文件。

**1) 先确认已有服务能被应用接受**（都是只读探测）：

```bash
# PostgreSQL 必须 ≥ 15：schema 用了 UNIQUE NULLS NOT DISTINCT（`scripts/init_db.sh` 注释写的 PG13+ 不准确）
# 且需要 pg_trgm（不依赖 pgvector：向量都在 Qdrant，PG 只用 pg_trgm 做模糊匹配）
psql -U postgres -tc "show server_version;"
psql -U postgres -tc "select name from pg_available_extensions where name='pg_trgm';"

# Qdrant 服务端版本：应用钉了 qdrant-client>=1.9,<1.10（hello-agents 0.2.9 要 import SearchRequest），
# 官方编排的服务端是 v1.12.4（已验证区间）；与它偏差较大时先在预发环境验证向量检索能命中再上生产
curl -s http://127.0.0.1:6333/ | head -c 200

# Redis：应用只写 `rl:chat:*` 限流键（无 flush），但共用实例建议给独立 db index；
# requirepass 的密码含 @ : / 时必须 URL 编码
redis-cli info server | grep redis_version
```

Neo4j 不需要（`MEMORY_TYPES` 默认不含 `semantic`）；如需语义记忆再单独补。

**2) 根 `.env`**：backend 容器走 `network_mode: host`，所以**中间件不用改监听**，`.env` 里直接写回环地址：

```ini
APP_ENV=production
JWT_SECRET=<openssl rand -base64 48>
DATA_ROOT=/srv/smart-cs
DATABASE_URL=postgresql+asyncpg://cs_user:<强密码>@127.0.0.1:5432/cs_db
REDIS_URL=redis://:<requirepass密码>@127.0.0.1:6379/7
QDRANT_URL=http://127.0.0.1:6333
QDRANT_API_KEY=<若有>
TRUSTED_PROXIES=127.0.0.1
ALLOWED_ORIGINS=https://cs.example.com
```

> `TRUSTED_PROXIES` 在 `docker-compose.external.yml` 里已写死 `127.0.0.1`（不继承基础编排的 `172.28.0.10/32`）；
> 反代与后端不同机时改反代所在机器的 IP/CIDR。`API_DOCS_ENABLED` 该文件默认 `false`。

**3) 建库与初始化**（首次部署才需要；存量库直接跳到 §5 升级流程）：

```bash
# 建用户/库（或用 psql 手工建，注意需是 PostgreSQL ≥ 15 的那个集群）
sudo -u postgres psql -c "CREATE USER cs_user WITH PASSWORD '<强密码>';"
sudo -u postgres psql -c "CREATE DATABASE cs_db OWNER cs_user;"
sudo -u postgres psql -d cs_db -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"   # 需超管执行

# 建表（空库时 app.migrate 直接用 scripts/init_db.sql 建到最新结构）
docker compose -f docker-compose.external.yml run --rm backend python -m app.migrate

# 初始化管理员：SEED_DEMO=false 时只建管理员，SEED_ADMIN_PASSWORD 留空则随机生成且只打印一次
docker compose -f docker-compose.external.yml run --rm backend python ../scripts/seed_data.py
```

> 一次性命令里**不要**用 `python -m app.setup --env-file ../.env`：`.env` 是以环境变量注入容器的，
> 容器内并没有这个文件。需要非交互建管理员可用 `run --rm backend python -m app.setup --username admin --password '...'`。

**4) 起后端**（数据目录先给 uid 1000 属主，容器内非 root 运行）：

```bash
sudo mkdir -p $DATA_ROOT/backenddata/knowledge_bases $DATA_ROOT/backenddata/doc_archives $DATA_ROOT/memorydata
sudo chown -R 1000:1000 $DATA_ROOT/backenddata $DATA_ROOT/memorydata
docker compose -f docker-compose.external.yml up -d --build backend
docker compose -f docker-compose.external.yml logs -f backend    # 看「[自检] 数据库 / Redis / Qdrant / 模型」
```

该文件把 uvicorn 覆盖为 `--host 127.0.0.1`：host 网络下沿用镜像默认的 `0.0.0.0` 会把 8000 直接挂到公网网卡。
端口被占先查：`ss -lntp | grep ':8000'`。

**5) 前端静态交给宿主机 Nginx**（不构建 web 容器）：

```bash
npm ci
npm run build -w frontend                                  # → frontend/dist/*
VITE_BASE=/admin/ npm run build -w admin-frontend          # → admin-frontend/dist/*
sudo mkdir -p /srv/smart-cs/web/admin
sudo rsync -a --delete frontend/dist/ /srv/smart-cs/web/
sudo rsync -a --delete admin-frontend/dist/ /srv/smart-cs/web/admin/
```

站点配置以 [docker/nginx.host.example.conf](../docker/nginx.host.example.conf) 为起点（与容器内 `docker/nginx.conf`
同源同行为：`/api/` 不缓冲供 SSE、`= /health` 直达后端、`/admin/` 与 `/` 各自 SPA 回退、`client_max_body_size 25m`）。
**admin 必须带 `VITE_BASE=/admin/` 构建**，否则资源与路由前缀全部错位。

**6) 验收**：

```bash
curl -fsS http://127.0.0.1:8000/health          # 后端直达
curl -fsS https://cs.example.com/health          # 经 nginx
curl -fsS https://cs.example.com/admin/          # 后台静态
curl -s  https://cs.example.com/api/v1/admin/custom-tools -o /dev/null -w '%{http_code}\n'   # 401/403 = 路由已在
```

客户端 IP 取信是否正确，别看 `docker compose ps`，看存下来的值：登录后台做一次写操作后
`psql -d cs_db -c "select ip, action, created_at from audit_logs order by created_at desc limit 3;"`
——`ip` 应是浏览器侧的真实 IP；若是 `127.0.0.1` 则 nginx 没下发 XFF 或 `TRUSTED_PROXIES` 不对
（`app/utils/request.py::client_ip` 只在直连对端命中白名单时才取 XFF 最左值；这一步搞错会让登录限流把
整台反代当同一个主体，错误密码 20 次锁定全站）。

**本形态与主流程的两点差异**（容易踩）：

- **备份脚本不能用**：`scripts/backup.sh` 写死了 `docker compose exec postgres pg_dump` 与
  `docker compose cp qdrant:/qdrant/snapshots/...`，没有这两个容器时会在第 1、2 步失败。本形态请改成宿主机命令：
  `pg_dump -U cs_user -d cs_db --no-owner --format=custom -f postgres.dump`、`redis-cli -a <pwd> BGSAVE` 后复制 RDB、
  Qdrant 快照仍可用 backend 调 API 创建（取文件从 Qdrant 自己的数据目录拿），`backenddata`/`memorydata` 直接 `rsync`。
- **已有容器要收敛**：若这台机器之前用基础编排跑过，先 `docker compose -f docker-compose.yml down` （**不加 `-v`**，
  命名卷里的旧数据还在），再把容器里的数据 `pg_dump` → 导入宿主机 PG、Qdrant 快照导入宿主机实例，才能改 URL。
  只改 `.env` 里的地址不会自动搬迁数据，表现为「升级后后台登录不进去 / 知识库空了」。

### 4.3 本地（或 CI）打镜像，服务器拉取启动

适用于服务器不想装 Node / 不便在服务器上拉 pip 依赖、或本机构建走代理更快。两条路线只改变
**backend 镜像的交付方式**；§4.2 的建库与迁移一步不变（镜像里带 `scripts/migrations`，升级后仍要在服务器跑一次
`run --rm backend python -m app.migrate`）。

前提：本机 Docker 引擎产出的就是 `linux/amd64`（`docker version --format "{{.Server.Os}}/{{.Server.Arch}}"`），与通用 x86
服务器一致；服务器是 ARM 时改用 `docker buildx build --platform linux/arm64`（走 QEMU，构建会明显变慢）。
镜像名统一由根 `.env` 的 **`BACKEND_IMAGE`** 控制（不设则为本地名 `smart-cs-backend:local`）。

**路线 A：私有镜像仓库**

```powershell
# 本地（仓库根目录，构建上下文必须是根，Dockerfile 里引用了 backend/ 与 scripts/）
docker build -f docker/backend.Dockerfile -t <registry>/<ns>/agentdock-backend:0.3.0 .
docker push <registry>/<ns>/agentdock-backend:0.3.0
```

```bash
# 服务器
docker login <registry>
echo 'BACKEND_IMAGE=<registry>/<ns>/agentdock-backend:0.3.0' >> .env
docker compose -f docker-compose.external.yml pull backend
docker compose -f docker-compose.external.yml up -d --no-build backend
```

- **仓库必须私有**：`backend.Dockerfile` 把整个 `backend/` 拷进镜像，镜像就是源码交付物（`.dockerignore` 只排掉
  `.env`、`backend/data/`、`backend/memory_data/`、`node_modules/`、`dist/`、`docs/`，库代码全在里面）；
  Docker Hub 新建仓库默认 public，推上去就撤不回来。项目许可为 CC BY-NC-SA 4.0，对外不可商用。
- 国内服务器从 Docker Hub 拉基础层经常超时：优先用阿里 ACR 个人版（免费）或自建 Harbor；GHCR 需要 Classic
  PAT（`write:packages` + `read:packages`），`docker login ghcr.io` 的用户名是 GitHub 账号名。
- tag 用发布号（`0.3.0`）而不是只用 `latest`：回滚就是改 `BACKEND_IMAGE` 指向旧 tag 再 `up -d --no-build`。

**路线 B：不经公网仓库，直接传 tar**

```powershell
docker build -f docker/backend.Dockerfile -t agentdock-backend:0.3.0 .
docker save -o backend-0.3.0.tar agentdock-backend:0.3.0        # 约 1.5 GB；PowerShell 无 gzip，要压缩用 7-Zip/WSL
scp backend-0.3.0.tar user@server:/tmp/
```

```bash
docker load -i /tmp/backend-0.3.0.tar
echo 'BACKEND_IMAGE=agentdock-backend:0.3.0' >> .env    # 不写的话 compose 去找默认的 smart-cs-backend:local
docker compose -f docker-compose.external.yml up -d --no-build backend
```

两侧用 `sha256sum backend-0.3.0.tar` 对一下。`scripts/release.py bundle` 是给「全栈离线包」写的（会连
postgres/qdrant/redis/web 一起 save，并生成带 web 段的 `docker-compose.offline.yml`），与本形态不匹配，只要
backend 就用上面的 save/load。

**前端静态要单独带上**（本形态下 dist 不在镜像里）：

```powershell
npm ci; npm run build -w frontend
VITE_BASE=/admin/ npm run build -w admin-frontend
scp -r frontend/dist/*     user@server:/tmp/web/
scp -r admin-frontend/dist/* user@server:/tmp/web/admin/
```

```bash
sudo rsync -a --delete /tmp/web/ /srv/smart-cs/web/
sudo nginx -t && sudo systemctl reload nginx
```

> 服务器上能 `git pull` 且有 Node 时，§4.2 第 5 步在服务器本地构建更省事。两条路线不要混用：
> 一旦出现「镜像是 A 版本、静态是 B 版本」，后台会报接口字段不存在，而不是样式错乱。

## 5. 升级与回滚

schema 升级走 `python -m app.migrate`（版本化增量迁移），不要用 `init_db.py` 硬凑，更不要在生产跑 `rebuild_and_seed.py`。

```bash
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

# 1) 先备份
COMPOSE_FILE="docker-compose.yml:docker-compose.prod.yml" bash scripts/backup.sh

# 2) 拉新代码
git pull

# 3) 看当前版本与待执行迁移（有待执行时退出码 1）
$COMPOSE run --rm backend python -m app.migrate --check

# 4) 事务内试跑后回滚，确认 SQL 无语法/依赖问题
$COMPOSE run --rm backend python -m app.migrate --dry-run

# 5) 正式执行（镜像内无 pg_dump，备份依赖第 1 步；本机装了 pg_dump 才用 --backup）
$COMPOSE run --rm backend python -m app.migrate

# 6) 重建镜像与容器
$COMPOSE up -d --build

# 7) 健康检查（对外路径是 /health；/api/health 不存在，会 404）
curl -fsS http://127.0.0.1:8080/health

# 8) 确认新代码已生效（/health 不回版本号，用路由探活）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/v1/admin/custom-tools   # 401/403=已上新代码，404=还在跑旧镜像
```

- 升级期间 `INIT_ON_START` 保持 `false`：`init_db.sql` 只负责空库建表，存量库版本由迁移推进。
- `MIGRATE_ON_START=true` 只建议单实例场景使用；多副本滚动更新时应独立执行第 3~5 步，避免并发迁移。
- **schema 只向前**：迁移不写回滚脚本，回滚镜像版本不等于回滚 schema。跨版本回退需要 `pg_restore` 到升级前的备份，先在预发环境演练。
- `APP_ENV=production` 时 `rebuild_and_seed.py` 会直接拒绝执行（`--force` 可绕过，但会 DROP 全部业务表）。
- 升级前确认已备份，备份产物至少包含 `postgres.dump` 与 `qdrant-*.snapshot`。
- **复用宿主机中间件的形态（§4.2）不涉及中间件升级**：只需
  `docker compose -f docker-compose.external.yml up -d --build backend`，前端把新的 `dist` 拷进 `/srv/smart-cs/web`（admin 进 `web/admin`）后
  `sudo nginx -t && sudo systemctl reload nginx`；上述第 1、3、5 步（备份与迁移）仍要跑，但备份得用宿主机命令取数。

## 6. 备份与恢复

### 6.1 备份

```bash
COMPOSE_FILE="docker-compose.yml:docker-compose.prod.yml" \
BACKUP_ROOT=/srv/smart-cs/backups \
BACKUP_RETAIN_DAYS=14 \
bash scripts/backup.sh
```

产物：

| 文件 | 内容 | 恢复方式 |
|---|---|---|
| `postgres.dump` | PostgreSQL 自定义格式 | `pg_restore` |
| `qdrant-*.snapshot` | Qdrant 全量快照 | Qdrant 快照恢复流程 |
| `backenddata/` | 知识库文档、原文归档 | 解包到 `DATA_ROOT/backenddata` |
| `memorydata/` | hello-agents 记忆 SQLite | 解包到 `DATA_ROOT/memorydata` |
| `redis.rdb` | Redis RDB | 替换 `/data/dump.rdb` |
| `manifest.txt` | 本次备份的元信息 | 人工核对 |

建议加 cron（示例：每天 3 点）：

```cron
0 3 * * * cd /srv/smart-cs && COMPOSE_FILE="docker-compose.yml:docker-compose.prod.yml" BACKUP_ROOT=/srv/smart-cs/backups bash scripts/backup.sh >> /var/log/agentdock-backup.log 2>&1
```

> 备份脚本要求服务处于运行状态（通过 `docker compose exec` 取数）。备份目录不要放在容器数据目录内。

### 6.2 恢复

先停写入方：`$COMPOSE stop backend web caddy`。

```bash
# PostgreSQL
$COMPOSE exec -T postgres pg_restore -U cs_user -d cs_db --clean --if-exists --no-owner \
  < /srv/smart-cs/backups/<ts>/postgres.dump

# Qdrant：使用官方快照恢复流程上传 qdrant-*.snapshot
# backenddata / memorydata：解包到 DATA_ROOT 对应目录后修正属主
chown -R 1000:1000 /srv/smart-cs/backenddata /srv/smart-cs/memorydata

# Redis：停 redis 后替换 /data/dump.rdb（AOF 开启时先处理 appendonlydir）
$COMPOSE start backend web caddy
```

恢复后验证：后台可登录、知识库文档列表完整、客户对话能命中知识、链路可写入。

## 7. 健康检查与监控

| 检查 | 方式 |
|---|---|
| 容器状态 | `docker compose ps`，五个服务均 `healthy`（生产另含 caddy） |
| Web 探针 | `curl -f http://127.0.0.1:8080/health`（基础编排）或 `https://<DOMAIN>/health` |
| 后端日志 | `docker compose logs -f backend`，启动应输出 `[自检] 数据库 / Redis / Qdrant / 模型` |
| 指标 | `GET /api/v1/admin/metrics`（需 admin Token）；`/metrics` 不对外暴露 |
| 链路 | 运营台「链路追踪」，按 `trace_id` 定位单轮 |

建议监控：错误率、首字延迟（TTFT）、路由低置信比例、活跃 SSE 连接数、磁盘使用率。

## 8. 容量与性能

- 后端镜像约 1.5 GB（含文档解析依赖），首次构建 5–8 分钟；
- 生产叠加默认内存上限合计约 6 GB，建议宿主机至少 8 GB；
- 磁盘主要增长点：PostgreSQL、Qdrant 向量、`backenddata` 原文、`request_traces`；
- 链路数据按轮增长，务必设置 `TRACE_RETENTION_DAYS` 并定期执行 `python scripts/cleanup_traces.py`（可挂 cron）；
- 单实例 SSE 连接上限约 500，超出会在指标中体现。

## 9. 上线检查清单

- [ ] 根 `.env` 已设置强 `JWT_SECRET`、`POSTGRES_PASSWORD`、`REDIS_PASSWORD`、`QDRANT_API_KEY`
- [ ] `APP_ENV=production`，`SEED_DEMO=false`
- [ ] 域名解析正确，80/443 可达，证书签发成功
- [ ] `docker compose ps` 全部 healthy，`/health` 返回 200
- [ ] 运营台可登录，客户可注册/登录，SSE 逐字输出
- [ ] 知识库上传、检索、重建索引正常
- [ ] 链路追踪能看到完整 spans，`TRACE_STORE_PAYLOAD` 符合合规要求
- [ ] 备份脚本已跑通，恢复流程在测试环境演练过
- [ ] 日志轮转、磁盘告警、`cleanup_traces` 定时任务已配置
- [ ] 演示账号已禁用或改密，后台无默认口令
