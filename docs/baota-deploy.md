# 宝塔 + 阿里云 ACR 部署参考（方案 B）

> 本文是一份**参考部署方式**：**客户前端在 `/smart-cs/`，管理后台在 `/smart-cs/admin/`，后端 API 走 `/cs/` 反代到 `127.0.0.1:8001`**。
> 适用于宝塔 Nginx + 云服务器 + 镜像仓库（本文以阿里云 ACR 为例）+ Docker Compose（后端单容器，PostgreSQL/Redis/Qdrant 由宿主机或云服务提供）。
> 文中域名、账号、命名空间、目录均为占位示例，按自己的环境替换。

## 0. 参考参数（示例值）

| 项目 | 值 |
|---|---|
| 域名 | `example.com` |
| 客户前端 | `https://example.com/smart-cs/` |
| 管理后台 | `https://example.com/smart-cs/admin/` |
| 后端 API | `https://example.com/cs/api/v1/...` |
| 后端实际地址 | `http://127.0.0.1:8001` |
| 后端镜像 | `registry.cn-hangzhou.aliyuncs.com/your-namespace/agentdock-backend:<tag>` |
| 客户前端静态目录 | `/srv/agentdock/default/smart-cs/frontend/` |
| 管理后台静态目录 | `/srv/agentdock/default/smart-cs/admin-frontend/` |
| 后端部署目录 | `/srv/agentdock/smart-cs/` |
| 后端 compose | `/srv/agentdock/smart-cs/docker-compose.server.yml` |
| Nginx 站点配置 | `/www/server/panel/vhost/nginx/example.com.conf` |

### 构建参数速查

| 应用 | `VITE_BASE` | `VITE_API_BASE` | 上传目录 |
|---|---|---|---|
| 客户前端 | `/smart-cs/` | `/cs/api/v1` | `/srv/agentdock/default/smart-cs/frontend/` |
| 管理后台 | `/smart-cs/admin/` | `/cs/api/v1` | `/srv/agentdock/default/smart-cs/admin-frontend/` |

> `VITE_BASE` 必须以 `/` 结尾；`VITE_API_BASE` 必须包含 `/api/v1`。
> 两个前端必须分别构建，不要把 `VITE_BASE` 混用。

---

## 1. 前置条件

1. ECS 已安装 Docker 与 Compose：
   ```bash
   docker version
   docker compose version
   ```
2. 已登录阿里云 ACR：
   ```bash
   docker login --username=your-acr-username registry.cn-hangzhou.aliyuncs.com
   ```
3. 后端 `.env` 已配置生产参数；`.env` 不要打进镜像。
4. PostgreSQL / Redis / Qdrant 可用：
   - PostgreSQL ≥ 15，目标库需要 `pg_trgm`
   - Redis 建议独立 DB index
   - Qdrant 服务端建议对齐 `v1.12.4`（项目依赖 `qdrant-client<1.10`）
5. 后端数据目录已创建并授权：
   ```bash
   mkdir -p /srv/agentdock/smart-cs/backenddata/knowledge_bases \
            /srv/agentdock/smart-cs/backenddata/doc_archives \
            /srv/agentdock/smart-cs/memorydata
   chown -R 1000:1000 /srv/agentdock/smart-cs/backenddata /srv/agentdock/smart-cs/memorydata
   ```

### 后端 `.env` 关键项

```ini
APP_ENV=production
APP_PORT=8001
DATA_ROOT=/srv/agentdock/smart-cs

BACKEND_IMAGE=registry.cn-hangzhou.aliyuncs.com/your-namespace/agentdock-backend:0.3.0
JWT_SECRET=<强随机值>
SEED_DEMO=false
API_DOCS_ENABLED=false

DATABASE_URL=postgresql+asyncpg://cs_user:<密码>@127.0.0.1:5432/cs_db
REDIS_URL=redis://:<密码>@127.0.0.1:6379/7
QDRANT_URL=http://127.0.0.1:6333
QDRANT_API_KEY=<如果有>

LLM_MODEL_ID=...
LLM_API_KEY=...
LLM_BASE_URL=...
EMBED_MODEL_TYPE=dashscope
EMBED_API_KEY=...
EMBED_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

ALLOWED_ORIGINS=https://example.com
TRUSTED_PROXIES=127.0.0.1
```

> 测试环境需要演示数据时设置 `SEED_DEMO=true`；正式上线改回 `false`。

---

## 2. 后端部署

### 2.1 本地构建并推送镜像

在本地仓库根目录：

```powershell
cd D:\project\smart-cs

.\scripts\build_push_acr.bat -Tag 0.3.0 -InteractiveLogin -NoCache
```

- `-InteractiveLogin`：按提示输入 ACR Registry 密码
- 已手动 `docker login` 时可改用 `-SkipLogin`
- 脚本使用：
  ```text
  docker buildx build --provenance=false --sbom=false --load
  docker push
  ```
  避免阿里云 ACR 报 `unknown manifest class for application/vnd.oci.empty.v1+json`。

手动等价命令：

```powershell
docker buildx build `
  --platform linux/amd64 `
  --provenance=false `
  --sbom=false `
  --load `
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ `
  -f docker/backend.Dockerfile `
  -t registry.cn-hangzhou.aliyuncs.com/your-namespace/agentdock-backend:0.3.0 `
  .

docker push registry.cn-hangzhou.aliyuncs.com/your-namespace/agentdock-backend:0.3.0
```

### 2.2 服务器拉取并启动

```bash
cd /srv/agentdock/smart-cs

docker login registry.cn-hangzhou.aliyuncs.com
docker compose -f docker-compose.server.yml pull backend
docker compose -f docker-compose.server.yml up -d --force-recreate --no-build backend
```

查看状态：

```bash
docker compose -f docker-compose.server.yml ps
docker compose -f docker-compose.server.yml logs --tail=100 backend
curl -fsS http://127.0.0.1:8001/health
```

### 2.3 首次初始化数据库

```bash
cd /srv/agentdock/smart-cs

# 建表 + schema_meta（幂等）
docker compose -f docker-compose.server.yml run --rm backend python ../scripts/init_db.py

# 种子数据（默认生产基础数据；SEED_DEMO=true 时写演示账号/场景）
docker compose -f docker-compose.server.yml run --rm backend python ../scripts/seed_data.py
```

> 不要先跑 `seed_data.py` 再跑 `init_db.py`。  
> 半初始化库（有表但没有 `schema_meta`）用 `init_db.py`，不要直接用 `app.migrate` 的空库基线路径。

端口被占用时：

```bash
ss -lntp | grep ':8001'
```

修改 `.env` 的 `APP_PORT` 后重建：

```bash
docker compose -f docker-compose.server.yml up -d --force-recreate --no-build backend
```

---

## 3. 管理后台部署

### 3.1 构建

在本地仓库根目录：

```powershell
cd D:\project\smart-cs

$env:VITE_BASE = '/smart-cs/admin/'
$env:VITE_API_BASE = '/cs/api/v1'
npm.cmd run build -w admin-frontend
Remove-Item Env:VITE_BASE, Env:VITE_API_BASE
```

Windows PowerShell 执行策略会拦截 `npm.ps1`，所以使用 `npm.cmd`。

构建产物：

```text
D:\project\smart-cs\admin-frontend\dist
```

检查 `index.html` 必须引用：

```text
/smart-cs/admin/assets/...
```

命令：

```powershell
Get-Content .\admin-frontend\dist\index.html | Select-String "assets"
```

### 3.2 上传到服务器

目标目录：

```text
/srv/agentdock/default/smart-cs/admin-frontend/
```

把 `admin-frontend/dist` **里面的内容**上传到该目录，不要多套一层 `dist`。

正确结构：

```text
admin-frontend/
├── index.html
├── assets/
└── ...
```

建议先备份旧目录：

```bash
cd /srv/agentdock/default/smart-cs
mv admin-frontend admin-frontend.bak.$(date +%Y%m%d%H%M%S)
mkdir admin-frontend
```

上传后检查：

```bash
ls -la /srv/agentdock/default/smart-cs/admin-frontend/
grep -o '/smart-cs/admin/assets/[^"]*' \
  /srv/agentdock/default/smart-cs/admin-frontend/index.html
```

页面标题应为：

```text
AgentDock 管理后台
```

---

## 4. 客户前端部署

### 4.1 构建

```powershell
cd D:\project\smart-cs

$env:VITE_BASE = '/smart-cs/'
$env:VITE_API_BASE = '/cs/api/v1'
npm.cmd run build -w frontend
Remove-Item Env:VITE_BASE, Env:VITE_API_BASE
```

构建产物：

```text
D:\project\smart-cs\frontend\dist
```

检查 `index.html` 必须引用：

```text
/smart-cs/assets/...
```

### 4.2 上传到服务器

目标目录：

```text
/srv/agentdock/default/smart-cs/frontend/
```

上传 `frontend/dist` 里面的内容：

```text
frontend/
├── index.html
├── embed.html
├── assets/
├── widget.js
└── ...
```

建议先备份：

```bash
cd /srv/agentdock/default/smart-cs
mv frontend frontend.bak.$(date +%Y%m%d%H%M%S)
mkdir frontend
```

检查：

```bash
ls -la /srv/agentdock/default/smart-cs/frontend/
grep -o '/smart-cs/assets/[^"]*' \
  /srv/agentdock/default/smart-cs/frontend/index.html
```

页面标题应为：

```text
AgentDock
```

---

## 5. Nginx 配置（方案 B）

编辑：

```text
/www/server/panel/vhost/nginx/example.com.conf
```

确保包含下面两段静态目录配置，并且 `/smart-cs/admin/` 在 `/smart-cs/` 前面：

```nginx
# 管理后台：/smart-cs/admin/
location /smart-cs/admin/ {
    alias /srv/agentdock/default/smart-cs/admin-frontend/;
    index index.html;
    try_files $uri $uri/ /smart-cs/admin/index.html;
    add_header Cache-Control "no-cache, no-store, must-revalidate";
}

# 客户前端：/smart-cs/
location /smart-cs/ {
    alias /srv/agentdock/default/smart-cs/frontend/;
    index index.html;
    try_files $uri $uri/ /smart-cs/index.html;
    add_header Cache-Control "no-cache, no-store, must-revalidate";
}
```

后端反代：

```nginx
location ^~ /cs/ {
    proxy_pass http://127.0.0.1:8001/;

    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Real-Port $remote_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header REMOTE-HOST $remote_addr;

    proxy_connect_timeout 60s;
    proxy_send_timeout 3600s;
    proxy_read_timeout 3600s;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;

    # SSE 流式对话必须关闭缓冲
    proxy_buffering off;
    proxy_cache off;
}
```

关键点：

- `proxy_pass http://127.0.0.1:8001/;` 末尾的 `/` 不能少：
  ```text
  /cs/api/v1/auth/staff/login
  → http://127.0.0.1:8001/api/v1/auth/staff/login
  ```
- 当前配置里的 `/api/` 反代到 `127.0.0.1:8000`，smart-cs 不使用它；smart-cs 统一走 `/cs/api/v1`。如果 `/api/` 是别的应用在用，不要改；如果它本来就是给 smart-cs 的，可改成 8001。
- `alias` 路径和 `location` 路径都以 `/` 结尾。
- `try_files` 的 fallback 必须带完整公开路径：`/smart-cs/admin/index.html`、`/smart-cs/index.html`。

重载：

```bash
nginx -t && nginx -s reload
```

---

## 6. 验证

### 6.1 静态资源

```bash
curl -I https://example.com/smart-cs/
curl -I https://example.com/smart-cs/admin/

# 用实际构建出来的文件名替换
curl -I https://example.com/smart-cs/assets/<customer-js>
curl -I https://example.com/smart-cs/admin/assets/<admin-js>
```

应返回 `200`。

### 6.2 后端健康

```bash
curl -fsS https://example.com/cs/health
```

等价于：

```text
https://example.com/cs/health
→ http://127.0.0.1:8001/health
```

### 6.3 登录接口

管理后台：

```bash
curl -i -X POST https://example.com/cs/api/v1/auth/staff/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Admin@123"}'
```

客户：

```bash
curl -i -X POST https://example.com/cs/api/v1/auth/customer/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"Alice@123"}'
```

返回 JSON（200/401/422 都说明路由已通）；`502` 通常是后端没起或端口不对，`404` 通常是 `/cs/` 前缀或 proxy_pass 末尾斜杠不对。

### 6.4 浏览器

- 客户：`https://example.com/smart-cs/`
- 后台：`https://example.com/smart-cs/admin/`
- `Ctrl + F5` 强制刷新
- 确认页面标题分别为 `AgentDock` / `AgentDock 管理后台`
- 测试一轮 SSE 对话，确认逐字输出

---

## 7. 测试数据

`.env` 设置：

```ini
SEED_DEMO=true
```

然后执行：

```bash
cd /srv/agentdock/smart-cs

docker compose -f docker-compose.server.yml run --rm backend \
  python ../scripts/seed_data.py --skip-docs
```

确认 Qdrant/Embedding 正常后，再补演示文档向量：

```bash
docker compose -f docker-compose.server.yml run --rm backend \
  python ../scripts/seed_data.py --force-docs
```

演示账号：

```text
admin      / Admin@123
service01  / Agent@123
alice      / Alice@123
```

需要生成对话记忆测试数据时（后端必须已启动）：

```bash
docker compose -f docker-compose.server.yml cp scripts/seed_memory_via_chat.py \
  backend:/app/scripts/

docker compose -f docker-compose.server.yml exec backend \
  python ../scripts/seed_memory_via_chat.py \
  --base-url http://127.0.0.1:8001 \
  --username alice \
  --password Alice@123
```

> `seed_memory_via_chat.py` 会发起真实聊天并调用 LLM，只建议在测试环境使用。

---

## 8. 发布顺序 Checklist

1. 本地构建并推送后端镜像
2. 服务器 `docker compose pull backend` + `up -d --force-recreate --no-build`
3. 首次部署执行 `init_db.py` + `seed_data.py`
4. 构建并上传管理后台到 `admin-frontend/`
5. 构建并上传客户前端到 `frontend/`
6. `nginx -t && nginx -s reload`
7. 验证 `/cs/health`、两个登录接口、静态资源、SSE
8. 浏览器强刷，确认页面标题和 API 请求路径
9. 记录本次镜像 tag / git commit
10. 备份数据库和 Qdrant

---

## 9. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 页面加载 `/assets/...` 404 | `VITE_BASE` 没设置或路径不匹配；检查 `index.html` 是否引用 `/smart-cs/assets` 或 `/smart-cs/admin/assets` |
| 只上传 `index.html` 后仍然 404 | 必须上传整个 `dist`，assets 文件名带哈希，不能只换 HTML |
| API 请求 `/api/v1/...` 失败 | 线上后端走 `/cs/api/v1`；检查 `VITE_API_BASE` 和 `/cs/` 反代 |
| `/cs/api/...` 404 | `proxy_pass` 末尾斜杠缺失；应为 `http://127.0.0.1:8001/` |
| 502 Bad Gateway | 后端没启动 / 端口不是 8001 / 防火墙或容器网络问题 |
| SSE 不逐字输出 | `/cs/` 反代加 `proxy_buffering off;`、`proxy_cache off;` |
| `schema_meta` 不存在 | 执行 `init_db.py`；不要对半初始化库直接跑 `app.migrate` |
| `relation "staff_users" already exists` | 先跑了 seed/旧版建表；用 `init_db.py` 逐条跳过已存在对象 |
| `must be owner of schema public` | 用 PostgreSQL 超级用户/数据库 owner 执行授权或重建库 |
| ACR push 报 `unknown manifest class` | 构建加 `--provenance=false --sbom=false --load`，用脚本默认已处理 |
| `docker login` 报 `unauthorized` | 密码必须是 ACR「访问凭证」的 Registry 密码；`/v2/` 是正常的 Registry 探测路径 |
| Qdrant 向量检索异常 | 服务端尽量对齐 `v1.12.4`；先 `seed_data.py --skip-docs` 验证业务 |
| 页面标题不对 | `/smart-cs/` 和 `/smart-cs/admin/` 的 dist 放反了；检查 alias 和 index.html |
| 浏览器仍加载旧文件 | `Ctrl+F5`；检查 CDN/浏览器缓存；确认 index.html 已替换 |

---

## 10. 安全与运维

- `.env`、`.acr.local` 不要提交；仓库已用 `.env.*` / `*.local` 忽略。
- `JWT_SECRET` 使用强随机值；生产 `SEED_DEMO=false`、`API_DOCS_ENABLED=false`。
- ECS 安全组只放行 `22/80/443`，`8001` 只绑定回环。
- ACR 仓库保持私有；发布完成后可重置 Registry 密码。
- 定期备份 PostgreSQL、Qdrant snapshot、`backenddata/`、`memorydata/`。
- 升级前记录镜像 tag 和 git commit；schema 迁移只向前，回滚镜像不等于回滚 schema。