# 功能更新文档索引（Implemented Features）

> 本目录记录 **smart-cs 已落地** 的功能更新。每一篇对应一次相对 [Spec](../spec.md) 初版的迭代，
> 内容包含：背景与问题、设计方案、关键实现（文件/函数）、数据结构与契约、配置项、验证方式与已知边界。
>
> 这些文档反映 **当前代码的真实实现**，与 [spec.md](../spec.md) 中标注为「实现现状」的章节保持一致；
> 当二者冲突时，以本目录与代码为准。

## 迭代总览

| 编号 | 更新主题 | 一句话说明 | 文档 |
|------|----------|-----------|------|
| U01 | HelloAgents 0.2.9 集成与依赖适配 | 框架升级带来的记忆/RAG/MCP API 与依赖版本约束、`BaseTool` 适配层 | [01-helloagents-integration.md](01-helloagents-integration.md) |
| U02 | 多意图路由与追问续接 | 一条消息识别多个意图、串行分块下发、路由结合上下文续接上一轮 Agent | [02-multi-intent-routing.md](02-multi-intent-routing.md) |
| U03 | 动态 Agent 混合注册与路由实时化 | 代码 Agent + DB 动态 Agent 混合注册，后台新增即时可被路由 | [03-dynamic-agents.md](03-dynamic-agents.md) |
| U04 | 请求链路追踪（request_traces） | 一轮对话经过的 Agent/MCP/工具/RAG/记忆 全链路采集、过滤与后台可视化 | [04-request-tracing.md](04-request-tracing.md) |
| U05 | stdio 型本地 MCP 演示服务与固定数据 | 物流/退款本地 FastMCP 服务、跨环境路径解析、工具发现超时隔离 | [05-stdio-mcp-demo.md](05-stdio-mcp-demo.md) |
| U06 | 工单独立表与跨轮记忆 | 工单从 audit_logs 迁移到 tickets 表结构化增改查；业务 Agent 跨轮不失忆 | [06-tickets-cross-round-memory.md](06-tickets-cross-round-memory.md) |
| U07 | 人员/客户分表与双入口鉴权 | users 拆为 staff_users/customer_users，各自登录 + 客户自助注册 | [07-staff-customer-auth-split.md](07-staff-customer-auth-split.md) |
| U08 | 知识库管理增强 | namespace CRUD、原文归档下载、手工 FAQ 条目、分块浏览与高级检索 | [08-knowledge-base-admin.md](08-knowledge-base-admin.md) |
| U09 | 异步自定义工具执行机制修复 | 自定义 async 工具真正执行、回投主事件循环、落库可用 | [09-async-tool-execution.md](09-async-tool-execution.md) |
| U10 | 自定义工具管理与 MCP 服务描述 | 代码 BaseTool 目录扫描/登记到 tools 表供动态 Agent 绑定；MCP 增加描述字段 | [10-custom-tool-management.md](10-custom-tool-management.md) |
| U11 | 代码 Agent 信息透明与状态治理 | 列表/详情带出提示词与注册工具、查看详情、禁用真正生效、孤儿行容错与清理 | [11-code-agent-transparency-state.md](11-code-agent-transparency-state.md) |
| U12 | Agent 路由线索与兜底动态化 | `routing_keywords`/`routing_examples` 注入路由提示词，规则去具名化，兜底 Agent 由生效清单派生并回落一次 | [12-agent-routing-clues.md](12-agent-routing-clues.md) |
| U13 | 路由分发器后台只读视图 | 后台可查路由参数/提示词模板/实时生效提示词与各候选 Agent 的线索完整度（不可改） | [13-router-agent-readonly.md](13-router-agent-readonly.md) |
| U14 | 兜底意图应答通道 | 兜底意图带 `is_fallback` 标记不再被低置信阈值拦掉（`ROUTER_FALLBACK_ANSWER`）；knowledge_qa 认领「你是谁」类元问题 | [14-fallback-answer-path.md](14-fallback-answer-path.md) |
| U15 | 链路详情显示路由意图依据 | 每个意图回显命中的路由关键词与 `clue_basis`（命中/配了没中/没配），后台时间线单独成块 | [15-route-intent-evidence.md](15-route-intent-evidence.md) |
| U16 | 高频固定话术（硬模板 + 后台可配） | 身份/能力/探听三类在路由之前逐字应答（毫秒级、不进模型），规则入库可后台改，排版软约束统一进基类提示词 | [16-canned-scripts-db.md](16-canned-scripts-db.md) |
| U17 | IT Helpdesk 场景包与包启停（历史） | 第二个场景包（3 Agent + 4 工具 + 3 知识库）；manifest `features`、包资产归属与运营台整体启停；场景包机制已移除，本文仅作记录 | [17-it-helpdesk-pack.md](17-it-helpdesk-pack.md) |
| U18 | 接入方与 Key 归属（integrations） | 服务端 Key 从场景迁到接入方：多 Key 轮换、场景授权、访客按接入方命名空间隔离；移除场景 rotate-key | [18-integration-keys.md](18-integration-keys.md) |
| U19 | 匿名接入的接入方归属 | `integrations.public_code` + `data-integration` / `X-Integration-Code`：匿名嵌入也能确定接入方，访客写入 `integration_id` | [19-anonymous-integration-attribution.md](19-anonymous-integration-attribution.md) |
| U20 | 单租户降级与自定义埋点 | 后台隐藏接入方入口、默认接入方授权全部场景、嵌入不再带 `data-integration`；`ChatRequest.ext` + `chat_events` 全量埋点（预留 `tenant_id` / `total_tokens`） | [20-single-tenant-degradation.md](20-single-tenant-degradation.md) |
| U21 | 场景身份话术、运行参数与登录人配额 | 身份/能力话术按场景复制（可改或改查知识库）；`scenes.runtime_config` 配检索/历史/场景额度；`created_by` + `staff_users.quota` 限创建数量，超限 3006 | [21-scene-identity-runtime-quota.md](21-scene-identity-runtime-quota.md) |
| U22 | 用量计量与报表（页面已下线） | token 估算 + 与链路采样解耦的采集、`chat_usage_daily` 日聚合；原 `/admin/usage` 汇总/CSV 与后台页已并入 U25 并删除 | [22-usage-metering.md](22-usage-metering.md) |
| U23 | License 与版本分层 | 离线 Ed25519 授权校验（只降级不阻断）、5 个功能位与 `require_feature()` 挂载点、`DEPLOYMENT_ID` 指纹、`/admin/license` 与后台授权页 | [23-license-edition.md](23-license-edition.md) |
| U24 | 评测与回归 | 用例集进 Git + 真实 HTTP 链路执行器、服务端链路回读、分母纪律与用例集指纹、基线 diff 与退化退出码、`eval_runs` 运行记录（schema v8） | [24-eval-regression.md](24-eval-regression.md) |
| U25 | 数据分析（成效指标 + 请求量） | 新菜单 `analytics` + `/admin/analytics/outcomes`：全量三率 + 每日请求量（日聚合，可对账）与链路三率（traces，标采样口径），空数据不伪装 0%；并入原用量报表的日统计、移除 token 展示与该页，零 schema 变更 | [25-analytics-outcomes.md](25-analytics-outcomes.md) |

## 与三份主文档的关系

- [../spec.md](../spec.md) — 可执行开发规范（SSOT），已升级到 **v2.0** 修正过时内容并新增「实现现状」章节。
- [../api.md](../api.md) — 接口速查，已同步全部实际端点与 SSE 事件。
- [../runbook.md](../runbook.md) — 运维手册，已补充 Qdrant/Neo4j、admin-frontend、脚本清单与故障排查。
