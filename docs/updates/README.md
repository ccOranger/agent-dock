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

## 与三份主文档的关系

- [../spec.md](../spec.md) — 可执行开发规范（SSOT），已升级到 **v2.0** 修正过时内容并新增「实现现状」章节。
- [../api.md](../api.md) — 接口速查，已同步全部实际端点与 SSE 事件。
- [../runbook.md](../runbook.md) — 运维手册，已补充 Qdrant/Neo4j、admin-frontend、脚本清单与故障排查。
