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
- 首次启动自动执行 `init_db` + `seed`；之后每次启动都会跑（幂等）

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
$COMPOSE run --rm backend python ../scripts/init_db.py
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

## 5. 升级与回滚

```bash
# 1) 先备份
COMPOSE_FILE="docker-compose.yml:docker-compose.prod.yml" bash scripts/backup.sh

# 2) 拉新代码
git pull

# 3) 重建
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$COMPOSE up -d --build

# 4) 有 schema 变更时补跑一次（幂等）
$COMPOSE run --rm backend python ../scripts/init_db.py
```

- 升级期间 `INIT_ON_START` 保持 `false`，不要靠重启自动迁移。
- 回滚：`git checkout <上一个 tag>` 后重新 `up -d --build`；数据库回滚用备份 `pg_restore`。当前没有版本化迁移，跨版本回滚前先在预发环境验证。
- 升级前确认已备份，备份产物至少包含 `postgres.dump` 与 `qdrant-*.snapshot`。

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
