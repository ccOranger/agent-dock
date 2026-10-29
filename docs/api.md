# API 速查（v2.0）

Base URL：`/api/v1`，响应统一为 `{ "code": 0, "message": "ok", "data": {...} }`（SSE / CSV / 文件下载 / Prometheus 除外）。
Swagger：`http://localhost:8000/docs`

**鉴权角色**（`app/api/deps.py`）：

- `current_customer` —— 客户端客户（`customer_users`）
- `require_staff` —— 后台人员，`agent` 或 `admin` 均可（`staff_users`）
- `require_admin` —— 仅 `admin`
- `require_perm(模块, "read"|"write")` —— 按 RBAC 菜单矩阵校验，`admin` 直接放行；只有 `read` 授权的模块可查不可写。

> 下文各节标题里的 `require_admin` / `require_staff` 是历史标注：**当前后台写接口实际按 `require_perm(模块, "write")` 校验**，模块 key 与菜单一对应（见 [spec §12.2](spec.md#122-rbac-权限矩阵)与 [Admin · RBAC](#admin--rbac角色与菜单矩阵require_admin)）。未纳入 RBAC 的接入方管理（`/admin/integrations`）仍按 `require_admin`。

> 人员与客户分表后登录入口分离（[U07](updates/07-staff-customer-auth-split.md)）：后台走 `/auth/staff/login`，客户端走 `/auth/customer/login`（账号不存在返回可识别错误码 `2004` 以引导注册），客户端支持 `/auth/customer/register` 自助注册。Token 内含 `sub`(用户ID)+`role`，`/auth/refresh` 按 `role` 回查对应表。

## Auth

```
POST   /api/v1/auth/staff/login         # 后台人员登录（admin-frontend）→ 双 Token
POST   /api/v1/auth/customer/login      # 客户登录（frontend）；账号不存在→2004，引导注册
POST   /api/v1/auth/customer/register   # 客户自助注册，成功即下发 Token 自动登录
POST   /api/v1/auth/refresh             # 刷新 Token（body: refresh_token）
GET    /api/v1/auth/me                  # 当前用户（任意已登录角色）
```

## Public（无需登录，嵌入 / API 对接）

（[多场景开发文档](multi-scene-development.md) M5）

```
GET    /api/v1/public/scenes/{code}           # 公开场景配置：展示配置/是否允许匿名/鉴权方式；不含 Agent、提示词、知识库
POST   /api/v1/public/scenes/{code}/visitors  # 签发匿名访客（仅已发布场景；返回 visitor_id）
```

- 场景未发布（`publish_key` 为空）时，公开配置仍可读，但 `allow_anonymous=false`，签发访客返回 403；
- `visitor_id` 是给宿主页面存 localStorage 的稳定标识（对应 `visitors.external_key`），聊天时原样传入即可复用同一访客的会话与记忆；
- `publish_key` 是场景级公开标识（会出现在嵌入页源码里），不是密钥；服务端接入 key 只能用 `X-API-Key`，由接入方（`/integrations` 直达页）签发，明文只在签发响应回显一次；
- `X-Integration-Code`（或 `?integration=ic_xxx`）是接入方公开标识，用于匿名访客归属：带它签发的访客会写入 `integration_id`，同一场景下不同接入方互不干扰；无效标识 401、未授权场景 403、场景未开放匿名 401，不降级为匿名池。它同样不是密钥，可被伪造。

```bash
# 1. 读取公开配置（带接入方归属；也可用 ?integration=ic_xxx）
curl -H "X-Integration-Code: ic_xxx" http://localhost:8000/api/v1/public/scenes/mcd_order

# 2. 签发访客（返回 {"visitor_id":"v_xxx","scene_code":"mcd_order","integration_code":"ic_xxx"}）
curl -X POST -H "X-Integration-Code: ic_xxx" \
  http://localhost:8000/api/v1/public/scenes/mcd_order/visitors

# 3. 匿名嵌入聊天（SSE；scene_code 同时放在 query，供 CORS 中间件识别场景来源）
curl -N -X POST "http://localhost:8000/api/v1/chat/stream?scene_code=mcd_order" \
  -H "Content-Type: application/json" \
  -H "X-Scene-Code: mcd_order" \
  -H "X-Integration-Code: ic_xxx" \
  -H "X-Publish-Key: pk_xxx" \
  -d '{"scene_code":"mcd_order","visitor_id":"v_xxx","message":"我的订单到哪了"}'

# 4. 服务端 API key 对接（不需要 scene_code 时落到 default 场景）
#    Key 在 /integrations 签发；单租户默认接入方已授权全部场景
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_xxx" \
  -d '{"scene_code":"mcd_order","message":"你好"}'
```

前端嵌入只用一行 script（气泡 + iframe 隔离，主题/标题/欢迎语来自后台「接入」配置）：

```html
<script src="https://your-host/widget.js" data-scene="mcd_order"
        data-ext='{"platform":"web","channel":"purchase"}'></script>
```

单租户部署下**不需要**传 `data-integration`：后台已隐藏「接入方管理」入口与场景接入抽屉的归属选择，生成的代码也不带该属性。该属性仍兼容（公开标识可在 `/integrations` 直达页获取），多接入方/多租户时再启用。

`data-ext` 是扁平 JSON，组件会整体透传到聊天请求的 `ext`；渠道等设备信息（如 `platform`）就作为其中的普通键，服务端不单独建列。它只是**客户端声明**，不能用于身份或隔离。

组件只向宿主暴露 `window.AgentDockWidget.open()` / `close()` / `toggle()`；宿主页可监听 `cs:widget:done` 等 postMessage 事件。开发环境演示页：`http://localhost:5173/embed-demo.html`。

> 定位分工：气泡只承担匿名接入（`visitor_id` 无鉴权，不代表宿主登录客户）。需要识别宿主客户时，由宿主站自研前端携带 customer token（`Authorization`）直接对接聊天接口；或由宿主后端用 `X-API-Key` 做 BFF 代理、把宿主客户 id 作为 `visitor_id` 下传。不要把客户 id 直接透传到浏览器侧当身份使用。

## Chat（核心）

```
POST   /api/v1/chat/stream              # SSE 流式对话
POST   /api/v1/chat/message             # 非流式备用，返回 {data, trace_id}
```

请求体（`ChatRequest`）：

```json
{
  "scene_code": "mcd_order",
  "session_id": null,
  "visitor_id": "v_xxx",
  "message": "我的订单到哪了",
  "ext": {"platform": "web", "channel": "purchase"}
}
```

`scene_code` 缺省 = 默认场景（`default`，单 Agent 直连，不路由）；未知 3003、停用 3004、无可用 Agent 3005，不会静默回落其它场景。

`ext` 是自定义埋点上下文：扁平 JSON，最多 10 个字段，字段名 `^[a-z][a-z0-9_]{0,31}$`，值只能是字符串/整数/布尔（字符串 ≤128 字符），序列化后 ≤1KB，`token/password/authorization/secret/api_key/access_token/refresh_token` 等敏感键直接 422。服务端把 `ext` 原样存入 `chat_events.ext`（JSONB），不再拆列；它不参与身份、场景隔离与限流分桶。

**鉴权顺序**（登录客户 / 服务端 key / 接入方公开标识 / 嵌入发布标识 / 已发布匿名，五者取一）：

1. `Authorization: Bearer <customer token>` → 客户身份（`user:<id>`）；带了但无效仍报 401，不降级为匿名；
2. `X-API-Key: sk_xxx` → 按 `key_hash` 反查启用中的接入方，再校验该接入方是否被授权到目标场景：Key 无效/接入方停用 401，未授权该场景 403；
3. `X-Integration-Code: ic_xxx` → 按公开标识反查启用中的接入方，用于匿名访客归属；无效标识 401、未授权场景 403、场景未开放匿名 401，不降级为匿名池；
4. `X-Publish-Key: pk_xxx` → 公开标识，主动传错按 401 处理；
5. 场景已发布（`publish_key` 非空）→ 匿名访客放行（`visitor:<id>`）；
6. 都没有 → 401/2001。

> Key 归属接入方而不是场景：一个接入方可持多把 Key（轮换过渡期新旧并存）并被授权到多个场景。访客按 `integration_id` 分命名空间，同一 `visitor_id` 在不同接入方下互不影响；不带 Key 和公开标识的匿名访客 `integration_id` 为空。

> 登录客户优先于接入方标识：带客户 token 时不会解析 `X-API-Key` / `X-Integration-Code`。公开标识仅做归属，不是安全凭证；强隔离请用 `X-API-Key`。

**限流**：Redis 分钟频次 / 日配额 / 并发流三档（默认 30 次/分、2000 次/日、3 条并发），按 `scene + user/visitor/ip` 分桶；超限 4029/429，Redis 故障时放行（fail-open）。此外还有**场景总额度**（`runtime_config.quota`，默认 0 = 不限），与主体限流同为 5 个桶一次 pipeline 提交，任一超限对本次已自增的键回滚。SSE 在流结束（含异常/断连）时释放并发槽位。

**对话埋点**：鉴权通过后、限流之前旁路写入 `chat_events`（全量，不受 `request_traces` 采样影响），记录 `trace_id`、场景、身份、`ext` 与消息长度；不存消息正文与 IP。被限流的请求同样留痕；写入失败只告警，不影响对话。首轮 `session_id` 与匿名 `visitor_id` 在进入 service 拿到真实会话/访客后按 `trace_id` 回填，旧值与无效值不会被写成 NULL（[U20](updates/20-single-tenant-degradation.md)）。

**跨域**：嵌入页需在请求 URL 带 `?scene_code=` 或请求头带 `X-Scene-Code`（中间件不读 body）；Origin 必须在全局 `ALLOWED_ORIGINS` 或该场景 `allowed_origins` 中，未授权 preflight 返回 403/2003。

> 入口对 `message` 做 `detect_injection`，命中抛 `4003 UnsafeInput`。响应头带 `X-Trace-Id`，与落库链路对齐（[U04](updates/04-request-tracing.md)）。

## Sessions（客户，current_customer，仅本人）

```
GET    /api/v1/sessions                 # 会话列表（分页 page/page_size）
POST   /api/v1/sessions                 # 新建会话（body: title?）
GET    /api/v1/sessions/{id}            # 会话详情
PATCH  /api/v1/sessions/{id}            # 更新标题/状态
DELETE /api/v1/sessions/{id}            # 删除会话
GET    /api/v1/sessions/{id}/messages   # 历史消息（分页）
```

## Memory（current_customer / require_staff）

```
GET    /api/v1/memory/me                # 自己的记忆（可 ?type=）
GET    /api/v1/memory/users/{user_id}   # 某用户记忆（staff）
POST   /api/v1/memory/search            # 语义检索（staff；body: user_id?, query, limit）
PATCH  /api/v1/memory/{id}              # 修正记忆内容/重要性（staff）
DELETE /api/v1/memory/{id}              # 遗忘记忆（staff）
```

## Knowledge（读 require_staff，写 require_admin）

命名空间（[U08](updates/08-knowledge-base-admin.md)）：

```
GET    /api/v1/knowledge/bases                    # 列表（附 document_count）
POST   /api/v1/knowledge/bases                    # 新建命名空间（^[A-Za-z0-9_]+$，重名 3002）
PATCH  /api/v1/knowledge/bases/{namespace}        # 改展示名/描述/启停
DELETE /api/v1/knowledge/bases/{namespace}        # 删除（仍有文档→3002，需先删文档）
```

文档：

```
POST   /api/v1/knowledge/documents                # 上传（multipart: file, namespace?, title?；admin）
GET    /api/v1/knowledge/documents                # 列表（?namespace/status/kind + 分页）
GET    /api/v1/knowledge/documents/{id}/raw       # 下载原文（FileResponse；无归档→3001）
GET    /api/v1/knowledge/documents/{id}/chunks    # 该文档已索引分块（游标 ?offset/limit）
DELETE /api/v1/knowledge/documents/{id}           # 删除（清 Qdrant 向量 + 归档 + DB，admin）
POST   /api/v1/knowledge/documents/{id}/reindex   # 重建索引（admin）
GET    /api/v1/knowledge/chunks                   # 整库分块浏览（?namespace + ?offset/limit）
```

手工 FAQ 条目（kind=manual）与检索：

```
POST   /api/v1/knowledge/entries                  # 新建手工文本条目（admin）
PUT    /api/v1/knowledge/entries/{id}             # 编辑手工条目（仅 manual；先清旧向量再重灌）
POST   /api/v1/knowledge/search                   # 检索测试（body: namespace, query, top_k?, min_score?, advanced?）
```

## Admin · Agents（代码 + 动态混合注册，require_admin 写）

（[U03](updates/03-dynamic-agents.md)、[U11](updates/11-code-agent-transparency-state.md)、[U12](updates/12-agent-routing-clues.md)）

```
GET    /api/v1/admin/agents                 # 代码注册表 + DB 动态 Agent 合并（code 带出 system_prompt/registered_tools/custom_tool_names/mcp_tools；孤儿 code 行标 stale）
GET    /api/v1/admin/agents/{name}          # Agent 详情（代码/动态均含注册工具；stale 行回显 DB 镜像）
POST   /api/v1/admin/agents                 # 创建动态 Agent（重名/冲突→3002；校验 MCP 绑定、自定义工具与 namespace；可带 routing_keywords/routing_examples）
PUT    /api/v1/admin/agents/{name}          # 编辑动态 Agent（仅 source=dynamic 可编辑；routing_* 传入即更新，空串项自动剔除）
DELETE /api/v1/admin/agents/{name}          # 删除（在册代码 Agent 不可删→1001；代码已下线的孤儿行可删）
PATCH  /api/v1/admin/agents/{name}/toggle   # 启用/禁用（body: enabled；禁用后路由与解析真正不命中）
```

## Admin · Scenes（场景与嵌入配置，require_admin 写 / require_staff 读）

（[多场景开发文档](multi-scene-development.md) M2–M5）

```
GET    /api/v1/admin/scenes                         # 列表（含状态、Agent 数、路由模式、publish_key、今日请求/日额度）
GET    /api/v1/admin/scenes/options                 # 启用中的场景下拉（聊天入口/资源归属回显）
POST   /api/v1/admin/scenes                         # 新建（code 全局唯一；默认场景标识保留）
GET    /api/v1/admin/scenes/{id}                    # 详情（含 widget_config/allowed_origins/runtime_effective；服务端 Key 见接入方管理）
PATCH  /api/v1/admin/scenes/{id}                    # 编辑（默认场景只允许改展示名/描述/runtime_config）
PUT    /api/v1/admin/scenes/{id}/agents             # 绑定 Agent（全量替换，顺序即路由候选顺序）
GET    /api/v1/admin/scenes/{id}/agents             # 可绑定 Agent + 当前绑定状态
PUT    /api/v1/admin/scenes/{id}/widget             # 嵌入配置/允许来源/发布开关
DELETE /api/v1/admin/scenes/{id}                    # 删除（默认场景不可删；有关联会话等→3002）
```

**运行参数 `runtime_config`**（[U21](updates/21-scene-identity-runtime-quota.md)）：JSONB，只存显式覆盖项，`{}` / 缺失 = 全部继承全局。

```json
{
  "rag": {"inject": true, "top_k": 5, "min_score": 0.3, "timeout": 15, "namespaces": ["cs_order"]},
  "history_limit": 50,
  "quota": {"per_minute": 60, "daily": 5000}
}
```

- 白名单键：`rag.inject/top_k/min_score/timeout/namespaces`、`history_limit`、`quota.per_minute/daily`；未知键→400/1001（不静默丢弃）；
- 范围：`top_k` 1–20、`min_score` 0–1、`timeout` 1–60、`history_limit` 1–50、`quota.per_minute` 0–600、`quota.daily` 0–100000；
- `rag.namespaces` 必须是存在且启用中的知识库，最多 10 个；**显式配置时以场景为准**（覆盖 Agent 自身的 `rag_namespace(s)`，包括 `search_all_knowledge_bases`），与实际检索范围保持一致；未配置时才回到 Agent 声明；
- 整对象替换：`PATCH` 传 `{}` 或 `null` = 恢复全部继承；
- `quota.*` 例外：默认 `0`（不限），**不继承** `PUBLIC_CHAT_*`（两者统计对象不同：环境变量限单主体，场景额度限整场景）；
- 详情接口额外返回 `runtime_effective`（合并全局后的生效值）与 `runtime_sources`（每键 `scene`/`global`/`default`）。

```json
// GET /admin/scenes 列表新增字段
{
  "today_requests": 128,     // 本场景今日 chat_events 条数
  "quota_daily": 5000,       // 生效的日额度，0 = 不限
  "quota_source": "scene"    // scene=本场景覆盖 / default=未配置（不限）
}
```

`PUT /widget` 请求体（未传字段保持原值）：

```json
{
  "widget_config": {"title": "订单助手", "welcome": "您好", "theme_color": "#2b5aa0", "position": "right", "placeholder": "请输入您的问题", "show_branding": true},
  "allowed_origins": ["https://mall.example.com"],
  "allow_anonymous": true
}
```

- `widget_config` 归一化后全量替换：只保留已知键，文本限长，颜色只接受 `#RGB/#RRGGBB`，位置只接受 `left/right`；
- `allowed_origins` 只保留 `scheme://host[:port]`（粘贴带路径的页面地址会自动截断），非法来源 400/1001；
- `allow_anonymous=true` 时若场景尚未发布则生成 `pk_...`；已有 `publish_key` 保持不变；`false` 时清空（已嵌入页面立即失效）；
- 保存后立即失效 CORS 来源缓存，无需等待 TTL。

服务端 API Key 不再挂在场景上：场景只保留公开标识 `publish_key` 与来源白名单，调用凭证由接入方签发与吊销（后台菜单已隐藏，可直达 `/integrations`），见下节。

## Admin · Integrations（接入方与 Key，require_admin）

Key 的所有者是接入方（服务端调用方），场景只是被授权对象：一个接入方可持多把 Key（轮换过渡期新旧并存），
停用接入方会让名下全部 Key 立即失效。访客按接入方划分命名空间，不同接入方下相同客户标识互不影响。

```
GET    /api/v1/admin/integrations                    # 列表（附 key_count 与已授权场景）
POST   /api/v1/admin/integrations                    # 新建（名称唯一；scene_ids 需为已存在场景）
GET    /api/v1/admin/integrations/{id}               # 详情（含 keys 尾号与场景）
PUT    /api/v1/admin/integrations/{id}               # 更新名称/说明/状态/授权场景（不传 scene_ids 保持原授权）
DELETE /api/v1/admin/integrations/{id}               # 删除（已有访客会话→3002，请改为停用）
POST   /api/v1/admin/integrations/{id}/keys          # 签发 Key：明文仅本次响应出现，库里只存 sha256
DELETE /api/v1/admin/integrations/{id}/keys/{key_id} # 吊销 Key（立即失效）
```

- Key 明文形如 `sk_...`，响应同时带 `hint`（尾号）供后台区分多把 Key；
- `status=disabled` 后该接入方全部 Key 立即失效，重新启用即可恢复（Key 不删除）。

> 单租户降级（U20）：后台菜单已隐藏本页，但接口与前端路由 `/integrations` 保留；默认接入方授权全部场景，新建场景自动授权给所有启用接入方。

## Admin · Router（路由分发器，只读，require_admin）

（[U13](updates/13-router-agent-readonly.md)）路由 Agent 的定义在代码 + `.env` 中，**不入库也不可在线修改**，因此本组只有两个 GET（写方法返回 405）。

```
GET    /api/v1/admin/router            # 基本档案：职责/模型/意图上限/置信阈值/兜底候选与兜底应答模式（fallback_confidence、fallback_answer）/上下文与线索上限/提示词模板原文与占位符说明（editable 恒为 false）
GET    /api/v1/admin/router/prompt     # 当前生效提示词预览（按生效清单实时渲染）+ 每个候选 Agent 的线索与实际注入部分
```

## Admin · Custom Tools（代码自定义工具登记，require_admin 写 / require_staff 读）

（[U10](updates/10-custom-tool-management.md)）

```
GET    /api/v1/admin/custom-tools            # 扫描代码目录列出工具（含 registered/enabled/覆盖描述）
POST   /api/v1/admin/custom-tools            # 登记到 tools 表 type=custom（不在目录→1001；重名/MCP 占用→3002）
PATCH  /api/v1/admin/custom-tools/{name}/toggle  # 启用/禁用（body: enabled）
DELETE /api/v1/admin/custom-tools/{name}     # 取消登记（被动态 Agent 引用→1001）
```

## Admin · Canned Scripts（高频固定话术，require_admin 写 / require_staff 读）

（[U16](updates/16-canned-scripts-db.md)）命中即在路由与大模型之前逐字下发；运行时只读这张表（代码里无兜底规则，
空表/全部停用 = 该能力关闭）；规则只开放 `patterns`（问法主干，归一化后整句全等）与 `keywords`（包含即命中，
归一化后至少 2 字），**不开放正则**；两者全空→1001。列表长度上限 `patterns` 100 / `keywords` 60（超→422；
该上限必须高于种子规模，因为后台保存是把整份列表原样提交回来）。

**内容与来源**（[U21](updates/21-scene-identity-runtime-quota.md)）：

- `answer_source`：`text`（默认，逐字下发 `reply_text`）/ `kb`（检索知识库后按固定格式拼接，不经模型改写）；
- `kb_namespaces`：`kb` 模式的检索范围，去重后不能为空，且必须是启用中的知识库；切回 `text` 时保留不报错；
- `reply_text`：`text` 模式必填；`kb` 模式只当引导语/兜底文案，可为空。
- 更新接口按**合并后的最终状态**校验：单独把 `text` 模式的正文改成空串同样 400（否则该行仍会命中却产出空串，本轮静默走路由）。
- 占位符：`{{scene_name}}` / `{{scene_code}}` / `{{agent_names}}` 在命中时渲染；
- 身份/能力话术按场景独立：场景行（`scene_id` 非空）覆盖同 key 全局行，新建场景自动复制模板（只补缺失 key）。

```
GET    /api/v1/admin/canned-scripts            # 全量（含停用项），按 priority 升序；每行带 scene_code（场景已停用/归档也能定位行）
POST   /api/v1/admin/canned-scripts            # 新增（key 重复→3002；空规则/单字关键词/问法归一化后为空→1001；kb 未选库→1001）
PUT    /api/v1/admin/canned-scripts/{key}      # 更新（只改一侧时另一侧沿用已存值，避免改空规则）
PATCH  /api/v1/admin/canned-scripts/{key}/toggle  # 启用/停用（body: enabled）
DELETE /api/v1/admin/canned-scripts/{key}      # 删除（is_builtin 种子三条不可删→1001，只能停用）
POST   /api/v1/admin/canned-scripts/match      # 命中测试（body: query, scene_code?；不写库，返回 normalized/matched/key/reply_text/answer_source/kb_namespaces）
```

## Admin · Staff（人员与配额，require_admin）

（[U21](updates/21-scene-identity-runtime-quota.md)）配额只统计 `created_by = 当前登录人` 的资源；`created_by IS NULL` 的 seed 数据不占额度。

```
GET    /api/v1/admin/staff                   # 人员列表 + 配额 + 已用 + 剩余（remaining=null 表示不限）
POST   /api/v1/admin/staff                   # 新建（username 唯一、password ≥ 8、role ∈ {admin, agent}）
PATCH  /api/v1/admin/staff/{id}              # 改 display_name / role / roles / is_active / quota（quota 整对象替换）
POST   /api/v1/admin/staff/{id}/password     # 重置密码
```

- `roles`（角色 key 列表，如 `["agent"]`）作为多角色集合整体覆盖，`role` 列随之同步为主角色；不传时按 `role` 单角色起步。
- 实际可见菜单由这些角色在「角色与权限」里的矩阵决定（见 [Admin · RBAC](#admin--rbac角色与菜单矩阵require_admin)）。

```json
// 配额语义：键缺失或 null = 不限，0 = 禁止创建，> 0 = 上限
{
  "quota": {"max_scenes": 1, "max_agents": 2, "max_knowledge_bases": 2},
  "used": {"scenes": 1, "agents": 0, "knowledge_bases": 2},
  "remaining": {"scenes": 0, "agents": 2, "knowledge_bases": 0}
}
```

- 防自锁：不能停用/降级自己，不能修改自己的配额（→400/1001）；
- 不提供物理删除（历史资源仍挂 `created_by`），只停用；
- 已用数量由 3 条 `GROUP BY created_by` 一次取回，不按人循环 count；
- 超限创建在 `POST /admin/scenes`、`POST /admin/agents`、`POST /knowledge/bases` 返回 3006/403。

## Admin · MCP Servers（require_admin 写 / require_staff 读）

（[U05](updates/05-stdio-mcp-demo.md)）

```
GET    /api/v1/admin/mcp-servers                       # 列表（附 used_by_agents 反查）
POST   /api/v1/admin/mcp-servers                       # 新建（transport=sse|streamable_http|stdio；重名 3002）
PUT    /api/v1/admin/mcp-servers/{server_id}           # 更新配置/启停
DELETE /api/v1/admin/mcp-servers/{server_id}           # 删除（被动态 Agent 引用→1001）
POST   /api/v1/admin/mcp-servers/test                  # 连通性探测（不落库，返回 tool_count/tools）
GET    /api/v1/admin/mcp-servers/{server_id}/tools     # 按已存配置连接并列出全部工具（带 timeout）
```

## Admin · Tickets（通用工单，客服/IT 共用表，require_staff）

（[U06](updates/06-tickets-cross-round-memory.md)、[U17](updates/17-it-helpdesk-pack.md)，以 `ticket_no`（`TK-XXXXXXXX`）定位；IT 包写入 `category=it_*` 与 `metadata.pack=it_helpdesk`）

```
GET    /api/v1/admin/tickets                 # 列表（?user_id/status/category/urgency/keyword/date_from/date_to + 分页）
GET    /api/v1/admin/tickets/{ticket_no}     # 详情（含 updates 补录历史与 metadata；IT 工单带 pack=it_helpdesk）
PATCH  /api/v1/admin/tickets/{ticket_no}/status  # 流转状态（body: status, note?；note 追加进 updates）
POST   /api/v1/admin/tickets/{ticket_no}/notes   # 补录一条信息（与业务 update_ticket 同结构）
```

## Admin · Inquiries（跨客户咨询，只读，require_staff）

```
GET    /api/v1/admin/inquiries/sessions          # 跨客户会话列表（?user_id/status/last_route_to/date_from/date_to + 分页）
GET    /api/v1/admin/inquiries/sessions/export   # 导出 CSV（UTF-8 BOM，同过滤条件）
GET    /api/v1/admin/inquiries/sessions/{id}     # 会话详情（消息 + 每条回复的 trace_id）
```

## Admin · Traces（请求链路，只读，require_staff）

（[U04](updates/04-request-tracing.md)）

```
GET    /api/v1/admin/traces                  # 列表（不带 spans；?session_id/trace_id/user_id/agent_name/tool_name/mcp_server/outcome/has_error/min_latency_ms/date_from/date_to + 分页）
GET    /api/v1/admin/traces/usage             # 近 N 天依赖使用分布（?field=tool_names|agent_names|mcp_server_names, days）
                                              # 注意：这里指“链路里的工具/Agent 使用分布”，与已下线的用量报表无关
GET    /api/v1/admin/traces/sessions/{id}    # 某会话按轮次链路摘要（时间正序）
GET    /api/v1/admin/traces/{trace_ref}      # 单条详情（含 spans 时间线；trace_ref=行ID/HTTP trace_id/助手消息ID）
```

> `session_id` 与 `trace_id` 是两个参数，但后台列表页只有一个 ID 框：粘 **36 位会话 UUID** 按会话过滤，
> 粘 **16 位十六进制链路 ID**（日志/评测报告里的 `trace_id`，如 `93e20f49fa8e44f3`）自动按链路过滤。
> 两者形态不重叠；其它形态（如 17 位、带字母的会话串）返回 **400 `code=1001`** 而不是 500。

> 链路详情的 `spans` 中，`route.intent`（每个路由意图）除 `intent_index`/`confidence`/`reason`/`is_fallback` 外，
> 还带意图依据 `matched_keywords`（原句命中的路由关键词，**本地子串匹配、仅供参考**，路由实际由 LLM 语义判定）
> 与 `clue_basis`（`keyword_hit` / `keyword_miss` 配了关键词但未出现 / `no_keywords` 未配关键词），
> 见 [U15](updates/15-route-intent-evidence.md)。数据只存 `spans` JSONB，无额外列。

## Admin · Takeover（人工接管，require_staff）

会话状态机：`active` --低置信/escalate--> `handover`（排队）--claim--> `human_handling`（机器人让位、客户消息只落库）--release--> `active` | `closed`。

```
GET    /api/v1/admin/takeover/sessions                       # 队列（?status=queued|handling、scene_code + 分页；缺省两者都返回，排队最久优先）
GET    /api/v1/admin/takeover/sessions/{session_id}           # 详情 + 消息（?after_id= 增量轮询，客户端按 id 去重）
POST   /api/v1/admin/takeover/sessions/{session_id}/claim     # 坐席接入（handover → human_handling；同一坐席重复调用幂等）
POST   /api/v1/admin/takeover/sessions/{session_id}/messages  # 坐席回复（body: content，1–4000 字）
POST   /api/v1/admin/takeover/sessions/{session_id}/release   # 结束人工（body: to=bot|close，缺省 bot）
```

- 除 `claim` 外的写接口要求会话处于 `human_handling` 且操作者是接手人（`admin` 可代操作），否则 409 / 403；
- 坐席回复以 `role=assistant` + `metadata.sender_type=staff` 落库、`routed_to=staff`，机器人上下文会排除这些消息，避免复述坐席话术；
- 已被其他坐席接入时 `claim` 返回 409，不会抢占。

## Admin · Analytics（数据分析，需 `analytics` 的 `read`）

原「用量报表」已并入本页：请求数、转人工、错误与日维度统计统一从这里出；`/admin/usage/*` 与后台
`/usage` 页面已移除（[U25](updates/25-analytics-outcomes.md)）。

```
GET /api/v1/admin/analytics/outcomes   # 六个成效比率 + 每日请求量（?from=YYYY-MM-DD&to=&scene_code=）
```

响应结构：

```json
{
  "code": 0,
  "data": {
    "from": "2026-10-22", "to": "2026-10-28", "scene_code": null,
    "handover_rate":   { "value": 0.0714, "numerator": 3, "denominator": 42 },
    "resolution_rate":  { "value": 0.9048, "numerator": 38, "denominator": 42 },
    "error_rate":      { "value": 0.0238, "numerator": 1, "denominator": 42 },
    "tool_success_rate":{ "value": 0.9412, "numerator": 16, "denominator": 17 },
    "fallback_rate":   { "value": 0.05, "numerator": 1, "denominator": 20 },
    "rag_hit_rate":    { "value": 0.6, "numerator": 12, "denominator": 20 },
    "daily": [
      { "day": "2026-10-22", "requests": 8, "handoffs": 1, "errors": 0 },
      { "day": "2026-10-23", "requests": 6, "handoffs": 0, "errors": 1 }
    ],
    "meta": {
      "full_source": "chat_usage_daily", "trace_source": "request_traces",
      "trace_sample_rate": 1.0, "sampled": false, "requests": 42, "traces_covered": 20
    }
  }
}
```

- **全量三率 + 每日请求量**（`handover_rate` / `resolution_rate` / `error_rate` / `daily`）来自 `chat_usage_daily`，
  无采样偏差；`daily` 按天升序，字段白名单只有 `day/requests/handoffs/errors`，**不输出 token**（估算值，不能被当账单）；
  只返回有请求记录的日期，不补零行；页面的折线图按区间补 0（日聚合无行 = 当天 0 次请求）以保证时间轴连续。
- **链路三率**（`tool_success_rate` / `fallback_rate` / `rag_hit_rate`）来自 `request_traces`，分母是「走完链路并落库的请求」；
  `TRACE_SAMPLE_RATE < 1` 时 `meta.sampled=true`，工具成功率会被低估，前端挂「采样口径」提示，仅作参考。
- 解决率口径 = `(requests - handoffs - errors) / requests`，是「机器人自主应答率」，不代表用户满意度。
- 分母为 0 时对应 `value` 为 `null`（分子分母如实返回 0），前端显示「—」，不把「没有数据」当成 0%。
- 默认区间为最近 7 天（含今天）；日期格式非法、`from > to`、跨度超 `USAGE_QUERY_MAX_DAYS` 均 400（业务码 1001）。
- 无 `analytics` 读授权返回 403（业务码 2003）；**幻觉率本期不提供**（无 ground-truth，后续走离线评测）。

## Admin · RBAC（角色与菜单矩阵，require_admin）

（专题：[U26 后台 RBAC](updates/26-rbac-menu-matrix.md)；设计见 [spec §12.2](spec.md#122-rbac-权限矩阵)；可授权清单的单一事实源是 `app/core/rbac.py` 的 `MENU_MODULES`）

```
GET    /api/v1/admin/rbac/menus                         # 可授权模块清单（角色页矩阵的数据源）
GET    /api/v1/admin/rbac/roles                         # 角色列表（含 permissions 矩阵与在册人数）
POST   /api/v1/admin/rbac/roles                         # 新建角色（body: key/name/description?/permissions?）
PATCH  /api/v1/admin/rbac/roles/{role_id}               # 改名/改描述（key 与 is_system 不可改）
DELETE /api/v1/admin/rbac/roles/{role_id}               # 删除自定义角色（内置角色 400）
GET    /api/v1/admin/rbac/roles/{role_id}/permissions   # 读取 {menu_key: read|write}
PUT    /api/v1/admin/rbac/roles/{role_id}/permissions   # 整体替换矩阵（body: permissions）
```

- 权限只有两级：`read`（菜单可见 + 可查询）与 `write`（含 read）；非法 key 在接口层被丢弃，不报错（等级非 `read|write` 由 schema 拦为 422）；
- 角色里残留已下线菜单（如 `usage` / `license`）不影响运行，下次保存矩阵时按白名单清掉；
- 写操作落审计（`admin.rbac.role_create` / `role_delete` / `role_grant`）。

## Admin · Tools / Metrics

```
GET    /api/v1/admin/tools                  # 工具列表（require_staff）
GET    /api/v1/admin/metrics                # Prometheus 指标（require_admin，text/plain）
GET    /health                              # 存活探针
GET    /metrics                             # Prometheus 全局抓取端点
```

## SSE 事件类型（/chat/stream）

| event | data | 说明 |
|-------|------|------|
| `session` | `{session_id}` | 首帧会话 ID |
| `route` | `{agent_name, confidence, reason, intents, intent_total}` | 路由结果；`agent_name/confidence/reason` 为主意图，`intents` 为按主次排序的意图清单（最多 `ROUTER_MAX_INTENTS` 个，[U02](updates/02-multi-intent-routing.md)）；兜底意图额外带 `is_fallback: true`（[U14](updates/14-fallback-answer-path.md)）；每个意图额外带 `matched_keywords`/`clue_basis` 意图依据（[U15](updates/15-route-intent-evidence.md)） |
| `agent_start` | `{agent_name, intent_index, intent_total, confidence, reason}` | 一个意图分支开始应答 |
| `agent_end` | `{agent_name, intent_index, intent_total}` | 该意图分支应答结束 |
| `text` | `{content}` | 文本增量 |
| `tool_call` | `{id, name, args}` | 工具调用开始 |
| `tool_result` | `{id, name, result, error}` | 工具返回（[U09](updates/09-async-tool-execution.md) 真实执行） |
| `memory_write` | `{type, id}` | 记忆写入通知 |
| `handover` | `{reason, confidence}` | 转人工 |
| `done` | `{message_id, trace_id}` | 结束；`trace_id` 可在后台链路页定位本轮 |
| `error` | `{code, message}` | 错误 |

> 多意图：一条消息被识别为多个意图时，各业务 Agent **串行**执行，`intent_index > 0` 的分支前会先下发一段 `text`（`\n\n---\n\n`）作分块分隔；主意图置信度低于 `ROUTER_CONFIDENCE_THRESHOLD` 时整轮 `handover`，仅次要意图低于阈值则该分支直接丢弃。
>
> 例外（[U14](updates/14-fallback-answer-path.md)）：路由判定“不贴合任何 Agent”而给出的兜底意图（`is_fallback: true`，置信度为提示词约定的固定低值 0.3）**不受阈值影响**，默认由兜底 Agent 先应答；置 `ROUTER_FALLBACK_ANSWER=false` 可恢复“也转人工”的旧行为。

## 错误码（`app/core/exceptions.py`）

| code | HTTP | 异常 | 说明 |
|------|------|------|------|
| 0 | 200 | — | 成功 |
| 1001 | 400 | ParamError | 参数校验失败 |
| 2001 | 401 | Unauthorized | 未认证 |
| 2002 | 401 | TokenExpired | Token 过期 |
| 2003 | 403 | Forbidden | 无权限 |
| 2004 | 404 | AccountNotRegistered | 账号不存在，请先注册（客户登录引导） |
| 3001 | 404 | NotFound | 资源不存在 |
| 3002 | 409 | Conflict | 资源冲突（重名/仍被引用） |
| 3003 | 404 | SceneNotFound | 场景不存在（scene_code 错误） |
| 3004 | 404 | SceneDisabled | 场景已停用/归档 |
| 3005 | 409 | SceneNotConfigured | 场景未配置可用 Agent |
| 3006 | 403 | QuotaExceeded | 创建数量已达上限（场景/Agent/知识库） |
| 4001 | 400 | ToolParamError | 工具参数校验失败 |
| 4003 | 400 | UnsafeInput | 输入含不安全内容 |
| 4029 | 429 | RateLimited | 请求过于频繁 |
| 5001 | 500 | InternalError | 内部错误 |
| 5002 | 502 | LLMUnavailable | LLM 服务不可用 |
| 5003 | 503 | RagSearchFailed | RAGTool 检索失败 |
| 5004 | 504 | ToolTimeout | 工具调用超时 |
| 5005 | 500 | MemoryToolFailed | MemoryTool 调用失败 |
| 5006 | 500 | AgentNotRegistered | Agent 未注册 |
