# 场景包开发指南

> 面向二次开发者：如何新增一个场景包，以及包启停、导航显隐与演示数据的约定。
> 关联文档：[产品定位](./positioning.md)、[领域解耦清单](./domain-decoupling.md)、[API 速查](./api.md)、[开发清单](./development-checklist.md)。

## 1. 边界：什么该进包，什么留在内核

| 层 | 负责 | 例子 |
|---|---|---|
| 平台内核 | 路由、上下文工程、记忆、知识库、工具运行时、会话、链路、权限、工单存储 | `RouterAgent`、`Orchestrator`、`TicketRepo`、`/admin/*` 通用接口 |
| 场景包 | 领域 Agent、提示词、领域工具、示例数据、场景文案 | `it_incident`、`query_it_asset`、IT FAQ |

三条硬约束（与 [领域解耦清单](./domain-decoupling.md) 一致）：

1. 内核不得 `import app.packs.*`；包通过 `manifest.yaml` 反向注册，内核只消费 `packs/loader.py` 的返回值。
2. 包可以依赖平台能力（工单表、知识库、记忆、MCP），平台不感知包的业务语义。
3. 工具名、Agent 名是全局唯一键：新增包时先查 `/admin/custom-tools` 与 Agent 清单，避免与已有包/MCP 重名。

## 2. 目录约定

```
backend/app/packs/<pack>/
  manifest.yaml        # 唯一入口：声明包名、Agent、工具、知识库命名空间
  agents/              # 继承 BaseBusinessAgent 的代码 Agent
  prompts/             # 业务提示词（纯字符串，可被 Agent import）
  tools/               # 继承 BaseTool 的自定义工具（含进程内假数据）
  seeds/               # 可选：包内演示数据（当前演示数据集中在 scripts/seed_data.py）
  ui/                  # 可选：场景文案占位
```

`<pack>` 目录名与 manifest 的 `name` 应保持一致；缺省时 `name` 回退为目录名。

## 3. manifest 字段

```yaml
name: it_helpdesk                 # 包唯一标识；缺省取目录名
display_name: IT Helpdesk 场景包  # 运营台展示名；缺省同 name
version: 0.1.0                    # 包版本；缺省 0.0.0
enabled: true                     # false = 本进程不装载（运营台仍可看到但不可开启）
features:                         # 运营台通用能力声明，前端据此显隐导航/页面
  - tickets
agents:                           # module:Class 引用，必须继承 BaseBusinessAgent
  - app.packs.it_helpdesk.agents.knowledge_agent:ITKnowledgeAgent
  - app.packs.it_helpdesk.agents.incident_agent:ITIncidentAgent
  - app.packs.it_helpdesk.agents.asset_agent:ITAssetAgent
tools:                            # module:Class 引用，必须继承 BaseTool
  - app.packs.it_helpdesk.tools.ticket_tool:CreateItTicketTool
  - app.packs.it_helpdesk.tools.ticket_tool:UpdateItTicketTool
  - app.packs.it_helpdesk.tools.ticket_tool:EscalateItTool
  - app.packs.it_helpdesk.tools.asset_tool:QueryItAssetTool
knowledge:
  namespaces:                     # 包声明的知识库命名空间（去重后汇总）
    - it_default
    - it_network
    - it_assets
```

字段语义：

- `enabled` 只控制**装载**。`false` 时包内 Agent/工具不注册，知识库命名空间不进入汇总；运行中不能把它打开，需改 manifest 后重启。
- `features` 是前端导航的通用开关，目前支持 `tickets`（工单页与工单菜单）。新增能力时前后端约定同名 key，不要用业务包名硬编码。
- `agents` / `tools` 用 `module:Class` 字符串引用，加载器逐个 import；单条失败只跳过该条并记警告，不阻塞启动。
- 工具类必须有非空 `name` 与无参构造；`user_id` / `session_id` 由 Orchestrator 按参数声明自动注入。

## 4. 装载与注册链路

```
app/core/bootstrap.py
  └─ registry 读取 loader.get_code_agents()          # 代码 Agent 注册表
app/tools/custom_catalog.py
  └─ CATALOG = loader.get_custom_tools()             # 自定义工具目录
app/packs/loader.py
  ├─ scan_manifests()   # 全部清单（含停用包），供运营台展示
  ├─ load_manifests()   # 仅启用包，供运行时装载
  ├─ get_code_agent_packs() / get_custom_tool_packs()  # 资产 → 包归属
  └─ get_enabled_features()  # 启用包的 features 去重汇总
```

装载结果按进程缓存：改 manifest、加包、删包都需要**重启后端**生效，与代码 Agent 的行为一致。

## 5. 包启停（运营台）

运营台「场景包」页直接调用：

```
GET   /api/v1/admin/packs          # 聚合全部包（含 manifest 停用项），require_staff
PATCH /api/v1/admin/packs/{name}   # 整体启停，body: {"enabled": true|false}，require_admin
```

语义与边界：

- 包启用 = `manifest.enabled` 且包内**至少一个**代码 Agent 启用；部分启用时 PATCH 会把包内 Agent 整体对齐。
- 开关复用 `agents.enabled`，不新增 `pack_settings` 表；PATCH 只影响 manifest 声明的代码 Agent，动态 Agent 请在「Agent 管理」单独处理。
- 开启时自动补齐缺失的 `agents` 行，避免“DB 无行 = 默认启用”掩盖开关。
- 停用包内 Agent **不会卸载**已注册的工具，也不会删除知识库向量；彻底移除需改 manifest 并重启。
- `manifest.enabled=false` 的包可以正常停用（幂等），但开启返回 `409 / 3002`。

## 6. 前端导航显隐

`admin-frontend` 的 `packs` store 在布局挂载时拉取 `/admin/packs`，按启用包的 `features` 决定菜单与页面是否可见：

- 任一启用包声明 `tickets` → 显示「工单」入口；
- 接口失败时回退为全部显示，避免后端不可用导致后台无法操作；
- 新增能力（如 `assets`）时，在包的 `features` 与 `AdminLayout.vue` 的 `hasFeature(...)` 两处登记。

## 7. 新增一个包：操作清单

1. 复制 `backend/app/packs/it_helpdesk/` 为 `packs/<name>/`，改 `manifest.yaml` 的 `name` / `display_name` / 资产引用。
2. 实现 Agent（继承 `BaseBusinessAgent`，声明 `name`、`description`、`system_prompt`、`rag_namespace`、`routing_keywords`、`routing_examples`）。
3. 实现工具（继承 `BaseTool`，声明 `name`、`description`、`parameters`，`async run`）。
4. 在 `knowledge.namespaces` 声明知识库；在 `scripts/init_db.sql` 的 `knowledge_bases` 增加对应命名空间（或走后台建库）。
5. 在 `scripts/seed_data.py` 增加演示 Agent 行、文档归档行与示例工单（幂等按名称/工单号）。
6. 如需真实向量，另写可选脚本（参考 `scripts/seed_it_kb.py`），失败不阻断主流程。
7. 在运营台确认：场景包页出现新包、Agent 清单包含新 Agent、路由提示词候选包含新路由线索。
8. 补测试：manifest 装载、工具契约、包启停接口与 RBAC；跑 `pytest` 与两端构建。

## 8. 内置示例

### customer_service（客服/售后）

- 3 个 Agent：`knowledge_qa`、`order_service`、`complaint_handler`
- 9 个工具：订单、物流、退款、工单
- 命名空间：`cs_default`、`cs_order`、`cs_after_sales`

### it_helpdesk（IT 服务台，M4）

- 3 个 Agent：
  - `it_knowledge`：账号、VPN、邮箱、打印机、软件与安全规范；检索 `it_default` + `it_network` + `it_assets`
  - `it_incident`：故障报修，建单/补录/升级人工；检索 `it_default`
  - `it_asset`：资产台账查询（型号、SN、领用人、保修、IP/MAC）；检索 `it_assets`
- 4 个工具：`create_it_ticket`、`update_it_ticket`、`escalate_it`、`query_it_asset`
- 命名空间：`it_default`、`it_network`、`it_assets`
- 工单复用内核 `tickets` 表：`category=it_*`，`metadata.pack=it_helpdesk`
- 资产数据为进程内假数据（`tools/mock_assets.py`），可用 `IT-LT-1001`、`张伟`、`电脑` 等查询

演示数据：

```bash
# 建表 + 种子：IT Agent 行始终写入；SEED_DEMO=true 时另含 IT 文档归档行与 4 张示例工单
cd backend
.venv/Scripts/python.exe ../scripts/seed_data.py

# 可选：把 IT FAQ 真正写入向量库（需 embedding 服务可用；单条失败不阻断）
.venv/Scripts/python.exe ../scripts/seed_it_kb.py
```

演示问题（客户对话端）：

- “VPN 怎么连接，总是掉线怎么办” → `it_knowledge`
- “我的笔记本开不了机，一直黑屏” → `it_incident`（自动建单）
- “帮我查一下资产编号 IT-LT-1001 的信息” → `it_asset`

## 9. 验证与演示截图清单（M3-4）

截图统一放在 `docs/assets/screenshots/`，建议 1440×900、浏览器缩放 100%，只保留脱敏后的演示数据。

| 编号 | 页面/场景 | 验收点 |
|---|---|---|
| 01 | 运营台登录 | 双入口可用，后台身份正确 |
| 02 | 仪表盘 | 会话/工单/链路统计有数据 |
| 03 | 场景包 | `customer_service` 与 `it_helpdesk` 两张卡，开关与包内资产可见 |
| 04 | Agent 管理 | 6 个代码 Agent（含 3 个 IT Agent）与动态 Agent 并存 |
| 05 | 路由分发器 | 候选清单含 IT Agent，线索完整度非「仅描述」 |
| 06 | 知识库 | 6 个命名空间，IT 文档/FAQ 可检索 |
| 07 | IT 对话 | 故障描述触发建单，返回 `TK-` 工单号 |
| 08 | 工单管理 | IT 示例工单四种状态与分类可见 |
| 09 | 链路追踪 | 本轮 `it_incident` 路由 + `create_it_ticket` 工具 span |
| 10 | 记忆 | 同一会话跨轮补录后，工单更新可追溯 |
| 11 | 停用客服包 | 仅启用 IT 包时系统仍可对话/建单（M4-4 验收） |

## 10. 已知边界

- 包开关只切换**代码 Agent**；动态 Agent、已登记工具、知识库数据不随包卸载。
- 工具与 Agent 名全局唯一，包之间重名时先登记者生效，后者被跳过并记警告。
- 工单目前仍是内核表（共享 `TicketRepo`），包专有表与包级 API 前缀留到后续版本。
- 演示资产为只读快照，重启即恢复初始数据；生产接入请在包内替换为真实资产系统工具。
- 改 manifest、增删包需重启后端；运营台 PATCH 开关直接写 `agents.enabled`，下一轮对话即时生效。
