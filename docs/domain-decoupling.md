# 领域解耦清单

> 目标：让"客服"从内核中退出，成为一个可替换的场景包。
> 关联文档：[产品定位](./positioning.md)、[开源发布检查清单](./open-source-release.md)、[开发清单](./development-checklist.md)。
> 范围：`backend/app`、`admin-frontend/src`、`frontend/src`、`scripts`、`docs`。

## 0. 验收标准

满足以下四条，才算解耦完成：

1. 内核（`core/`、`agents/` 运行时、`services/`、`tools/` 基础设施）中不再出现客服专有概念。
2. 新增场景包只需新增 `packs/<name>/` 与 manifest，不修改内核代码。
3. 停用客服包、只启用 IT Helpdesk 包时，系统能正常启动、路由、对话、追踪。
4. 现有客服能力（工单、话术、订单工具）作为包功能保留，行为不回退。

## 1. 分层原则

| 层 | 职责 | 允许依赖 |
|---|---|---|
| 平台内核 | 路由、上下文工程、记忆、知识、工具运行时、会话、链路、权限 | 通用库、基础设施 |
| 场景包 | 领域 Agent、提示词、领域工具、种子数据、场景文案 | 平台内核 |
| 交付层 | 部署、配置、初始化、升级、白标 | 两者 |

三条硬约束：
- 内核不得 `import packs.*`；由包加载器反向注册。
- 内核代码不出现"客服/工单/订单/话术"等业务词（`sort_order` 这类同形词不算）。
- 包内数据表、包内 API 路由不得进核心 schema 与核心路由前缀。

## 2. 目标目录

```
backend/app/
  core/                 # 不变
  agents/
    orchestrator.py     # 内核
    router_agent.py     # 内核（提示词由包注入）
    registry.py         # 内核（改为包发现）
    base_business_agent.py
    business/dynamic_agent.py
  tools/                # base / catalog / loader / mcp_loader / factories
  services/             # chat / document / rag / qdrant / memory
  api/v1/               # chat / sessions / memory / knowledge / auth / admin*
  packs/
    customer_service/
      manifest.yaml
      agents/{knowledge_agent,order_agent,complaint_agent}.py
      prompts/{router,knowledge,order,complaint}.py
      tools/{order_tool,refund_tool,logistics_tool,ticket_tool,mock_data}.py
      seeds/{staff,docs,templates}.py
      ui/copy.yaml
    it_helpdesk/
      manifest.yaml
      ...
```

前端场景包化可后置：先用 manifest 的 `ui` 字段控制导航显隐与文案，再考虑目录级拆分。

前端形态已定：管理端 SPA + 聊天挂件。v0.1 用 npm workspaces 单仓多包共享 http/auth/SSE/types；v0.2 聊天端 iframe 挂件化；部署为一个 Nginx（`/` 聊天、`/admin/` 管理端）。详见定位文档 5.1。

## 3. 后端文件清单

### 3.1 平台内核（保留，少量改造）

| 文件 | 现状 | 动作 | 优先级 |
|---|---|---|---|
| `core/config.py` | 通用配置 + 客服相关阈值 | 拆出包级配置（工单状态、话术开关） | P1 |
| `core/context_engineer.py` | 含订单号/工单号/投诉词等信号规则 | 信号规则抽为包提供的 `signal_rules`，内核只执行 | P1 |
| `core/tracing.py` | 通用 | 无 | - |
| `core/bootstrap.py` | 初始化代码 Agent | 改为加载场景包 manifest | P1 |
| `core/security.py`、`login_guard.py`、`database.py`、`logging.py`、`exceptions.py` | 通用 | 无 | - |
| `agents/orchestrator.py` | 通用调度，注释含客服字样 | 仅清理文案 | P0 |
| `agents/router_agent.py` | 通用路由，提示词写死客服语境 | 提示词模板参数化，由包注入 | P1 |
| `agents/base_business_agent.py` | 通用 | 无 | - |
| `agents/registry.py` | 硬 import 三个客服 Agent | 改为包注册/发现，删除硬 import | P1 |
| `agents/llm_factory.py` | 通用 | 无 | - |
| `agents/business/dynamic_agent.py` | DB 驱动，通用 | 无 | - |
| `tools/custom/base.py`、`custom_catalog.py`、`custom_loader.py` | 通用工具基础设施 | 无 | - |
| `tools/mcp_loader.py`、`memory_factory.py`、`rag_factory.py` | 通用 | 无 | - |
| `services/chat_service.py` | 通用 | 无 | - |
| `services/document_service.py`、`rag_service.py`、`qdrant_helper.py`、`memory_service.py` | 通用 | 无 | - |
| `api/v1/chat.py`、`sessions.py`、`memory.py`、`knowledge.py`、`auth.py` | 通用 | 无 | - |
| `api/v1/admin.py` | Agent / MCP / 自定义工具 / metrics | 无 | - |
| `api/v1/admin_traces.py`、`admin_router.py` | 通用 | 无 | - |
| `api/v1/admin_inquiries.py` | 会话查看与导出（通用能力） | 页面文案从"咨询"改为"会话" | P2 |
| `models/entities.py` | 混合 | 拆出 Ticket/CannedScript，见第 4 节 | P2 |
| `models/repositories.py` | 约 1300 行，混合 Ticket/CannedScript/会话查询 | 拆出 `TicketRepo`、`CannedScriptRepo` 到包内 | P2 |
| `schemas/api.py` | 混合 Ticket/CannedScript/会话 DTO | 按包拆分 | P2 |

### 3.2 客服场景包（迁出内核）

| 文件 | 内容 | 目标位置 | 优先级 |
|---|---|---|---|
| `agents/business/knowledge_agent.py` | 知识问答 Agent | `packs/customer_service/agents/` 或保留为内核模板 | P0 |
| `agents/business/order_agent.py` | 订单 Agent | `packs/customer_service/agents/` | P0 |
| `agents/business/complaint_agent.py` | 投诉 Agent | `packs/customer_service/agents/` | P0 |
| `agents/prompts/knowledge.py`、`order.py`、`complaint.py` | 业务提示词 | `packs/customer_service/prompts/` | P0 |
| `agents/prompts/router.py` | 路由提示词（含客服语境） | 模板化后放包内 | P1 |
| `tools/custom/order_tool.py`、`refund_tool.py`、`logistics_tool.py`、`ticket_tool.py`、`mock_data.py` | 订单/退款/物流/工单 | `packs/customer_service/tools/` | P0 |
| `agents/canned_scripts.py` | 匹配引擎 + 客服种子规则 | 引擎留内核（改名 `response_templates.py`），种子进包 | P1 |
| `api/v1/admin_tickets.py` | 工单后台接口 | 包路由，挂到 `/api/v1/packs/customer-service/tickets` | P2 |
| `api/v1/admin_canned_scripts.py` | 话术后台接口 | 内核改为"应答模板"接口，种子由包提供 | P2 |
| `models/entities.py` 的 `Ticket`、`CannedScript` | 领域表 | Ticket 进包 schema；CannedScript 泛化为 `response_templates` | P2 |

### 3.3 脚本与文档

| 文件 | 归属 | 动作 |
|---|---|---|
| `scripts/seed_data.py` | 客服包种子 + 演示数据 | 拆为 `packs/customer_service/seeds/` 与通用演示数据 |
| `scripts/init_db.sql` | 核心表 + 领域表 | 核心表保留；tickets 等迁移到包初始化脚本 |
| `scripts/rebuild_and_seed.py`、`seed_memory_via_chat.py` | 演示 | 归入包或标注 demo |
| `docs/spec.md` | 客服系统规格 | 拆为平台规格 + 客服包规格 |
| `docs/api.md` | 混合 | 按核心 API / 包 API 分组 |
| `docs/updates/*` | 历史记录 | 保留，不改写 |

## 4. 数据库表归属

| 表 | 归属 | 处理建议 |
|---|---|---|
| `staff_users`、`customer_users` | 内核 | v0.2 合并为 `app_users` + `role`；v0.1 先不改 |
| `sessions`、`messages`、`memories` | 内核 | `assigned_agent` 保留为"转人工"通用能力 |
| `documents`、`knowledge_bases` | 内核 | 无 |
| `agents`、`tools` | 内核 | 无 |
| `audit_logs`、`request_traces` | 内核 | 无 |
| `tickets` | 客服包 | 迁到包初始化脚本；核心 schema 不再建表 |
| `canned_scripts` | 内核能力 + 包内容 | 表改名 `response_templates`；种子由包写入 |

说明：`tickets` 迁移涉及历史数据，建议在 v0.2 做，采用"新表 + 双写/迁移脚本 + 旧表只读"的渐进方式，不直接 drop。

## 5. API 路由归属

| 前缀 | 归属 | 动作 |
|---|---|---|
| `/api/v1/chat`、`/sessions`、`/memory`、`/knowledge`、`/auth` | 内核 | 无 |
| `/api/v1/admin/agents`、`/mcp-servers`、`/custom-tools`、`/tools`、`/metrics` | 内核 | 无 |
| `/api/v1/admin/traces`、`/router`、`/sessions` | 内核 | 无 |
| `/api/v1/admin/tickets` | 客服包 | 迁到 `/api/v1/packs/customer-service/tickets`，旧路径保留一版别名 |
| `/api/v1/admin/canned-scripts` | 内核能力 | 改为 `/admin/response-templates`，旧路径别名保留一版 |

## 6. 前端页面归属

| 页面 | 归属 | 动作 |
|---|---|---|
| `AgentList.vue`、`KnowledgeBaseView.vue`、`CustomToolsView.vue`、`McpServersView.vue` | 内核 | 无 |
| `MemoryView.vue`、`TracesView.vue`、`RouterInfoView.vue`、`Dashboard.vue` | 内核 | Dashboard 中的工单统计改为按包显隐 |
| `InquiriesView.vue` | 内核 | 改名为"会话管理"，文案去客服化 |
| `TicketsView.vue`、`CannedScriptsView.vue` | 客服包 | 导航按 manifest 显隐；话术页改名"应答模板" |
| `frontend/src/views/customer/*` | 内核 | 收敛为可嵌入聊天挂件，文案走包配置 |

## 7. 场景包契约（manifest）

M1 已落地最小可用子集，实际文件见 `backend/app/packs/customer_service/manifest.yaml`：

```yaml
name: customer_service
display_name: 客服/售后场景包
version: 0.1.0
enabled: true

# 运营台通用能力声明：任一启用包声明 tickets 即显示工单入口（M4）
features: [tickets]

# 包内代码 Agent：模块路径:类名，类需继承 BaseBusinessAgent，name 全局唯一
agents:
  - app.packs.customer_service.agents.knowledge_agent:KnowledgeAgent
  - app.packs.customer_service.agents.order_agent:OrderAgent
  - app.packs.customer_service.agents.complaint_agent:ComplaintAgent

# 包内自定义工具：模块路径:类名，类需继承 BaseTool，name 全局唯一
tools:
  - app.packs.customer_service.tools.order_tool:QueryOrderTool
  # …其余工具同类登记

# 领域知识库命名空间声明（P1 起用于检索范围收敛与后台分组）
knowledge:
  namespaces: [cs_default, cs_order, cs_after_sales]
```

字段说明：

| 字段 | 状态 | 内核如何使用 |
|---|---|---|
| `name` / `display_name` / `version` / `enabled` | 已实现 | `enabled: false` 时整包跳过，服务降级为“仅内核模式” |
| `agents` | 已实现 | `packs.loader.get_code_agents()` 导入并校验子类、name 唯一 |
| `tools` | 已实现 | `packs.loader.get_custom_tools()` → `tools/custom_catalog` |
| `knowledge.namespaces` | 已实现（声明） | `get_knowledge_namespaces()` 去重保序；检索收敛待 P1 |
| `features` | 已实现（M4） | 声明 `tickets` 等运营台能力；前端据此显隐导航，包启停聚合接口见 [场景包指南](./packs.md) |
| `response_templates` / `routing` / `ui` | 待 P2 | 话术、信号规则、前端导航目前仍在内核 |

内核只读这些通用字段，不解释业务含义；新增场景包时复制目录与 manifest 即可，内核零改动。

> 早期草案中的 `agents.code` / `tools.custom` 改成了 `module:Class` 引用：加载器需要真正 import 到类，而名字列表还得额外维护一张映射表，反而增加耦合。

## 8. 迁移顺序与工作量

| 阶段 | 内容 | 预计 | 风险 | 状态 |
|---|---|---|---|---|
| P0 代码归位 | 新建 `packs/`，移动提示词、领域工具与业务 Agent，更新 import | 0.5–1 天 | 低 | M1 已完成 |
| P1 注册与种子 | manifest + 包加载器；registry 去硬 import；种子拆包；路由提示词模板化 | 3–5 天 | 中 | 装载器与 registry 已完成；种子/路由模板待做 |
| P2 能力泛化 | 话术→应答模板；信号规则下沉；后台 IA 调整；API 别名 | 1–2 周 | 中 | 待做 |
| P3 数据模型 | `app_users`、Ticket 迁包、Session 外键调整 | 2–4 周 | 高，按需再做 | 未开始 |
| P4 多租户 | `tenant_id` 与隔离（仅 SaaS 时） | 3–6 周 | 高，暂不做 | 未开始 |

建议只做 P0–P2 就发布 v0.1；P3 放到 v0.2，P4 等 SaaS 验证后再启动。

## 9. 内核词汇门禁

发布前在 CI 增加一条建议性检查（先告警、不阻断）：

```bash
rg -n --pcre2 "客服|工单|订单|客户|话术|ticket|(?<!sort_)order|customer|canned" \
  backend/app/core backend/app/agents backend/app/services backend/app/tools
```

允许保留的例外需要显式注释，例如：
- `sort_order`、`order_by` 等排序语义。
- `handover`（转人工）作为通用能力名称。

目标是把内核命中数从当前的上百处降到个位数，且每一处都有理由。

## 10. 风险与回滚

| 风险 | 应对 |
|---|---|
| 移动文件导致 import 断裂 | P0 只改路径与 import，不改逻辑；每步跑全量 pytest |
| 包加载器引入启动顺序问题 | manifest 加载失败降级为"仅内核模式"，记录错误不阻塞启动 |
| 工单迁移丢数据 | 双写 + 迁移脚本 + 旧表只读保留一个版本 |
| 前端导航按包显隐后空白 | manifest 缺省值兜底，包禁用时回退默认导航 |
| 过早抽象 | 只做 P0–P2；P3/P4 由第二个真实场景触发 |
