# U11 · 代码 Agent 信息透明与状态治理

> 状态：已落地 ｜ 影响面：agents 注册表、路由/解析、管理后台列表/详情/删除、启动校验

## 1. 背景与问题

代码托管 Agent（`source=code`）的定义（system_prompt、注册工具、MCP/自定义工具绑定）都在 Python 类里，DB 只存一层「运营镜像」（开关/展示）。此前后台存在三处不透明、一处不严谨：

1. 列表/详情接口对代码 Agent **不返回** system_prompt、注册工具、自定义工具等（`system_prompt` 甚至被写死 `None`）；
2. 无「查看详情」入口，运营看不到某个 Agent 到底挂了哪些工具；
3. 禁用代码 Agent **不生效**——路由候选与运行时解析都忽略 `enabled`；
4. 反向孤儿场景：库里有 `source=code` 行但代码已下线时，启动直接 `RuntimeError` **阻断服务**，只能手动删库，体验差。

## 2. 注册工具静态解析（agents/registry.py）

代码 Agent 的 `build_tools` 内即时 `registry.register_tool(...)`，无法靠实例化枚举（memory/rag 工厂会连 Neo4j/Qdrant 产生副作用）。改用 **AST 静态解析**源码，不实例化：

```python
def _registered_tool_names(cls) -> list[str]:
    # 解析 build_tools 源码里的 registry.register_tool(<X>()) 调用
    # 自定义工具类 → 读类级 name；get_memory_tool/get_rag_tool 工厂 → 归一 "memory"/"rag"
```

`list_agents()` 据此扩展返回：`system_prompt` / `rag_namespace` / `rag_namespaces` / `search_all_knowledge_bases` / `mcp_tools` / `custom_tool_names`（目录存在的部分）/ `registered_tools`。

## 3. 详情接口与前端「查看详情」

- 后端 `GET /admin/agents/{name}` 代码分支直接透传 `list_agents` 字段（不再写死 `system_prompt=None`）；
- 前端 `views/AgentList.vue` 操作列对**所有** Agent 提供「详情」，弹出只读 `el-descriptions`（名称/来源/启用/模型/知识库/描述/MCP/自定义工具/注册工具/SystemPrompt）；`openDetail` 调 `apiGetAgentDetail`，失败回退列表行数据；
- 动态 Agent 详情也补齐 `registered_tools`（按运行时装配逻辑静态推导 = `memory` +（有主库时）`rag` + 已登记 `custom_tool_names`；MCP 属外部发现，单独在 `mcp_tools` 展示，不并入）。

## 4. 禁用真正生效（agents/registry.py）

修正「禁用代码 Agent 不生效」缺陷，路由与运行时两处均尊重 `enabled`：

- `list_effective_agents()`：查 `AgentRepo.list_all()` 取 `source=code 且 enabled=false` 集合，从代码清单**剔除**（DB 无行默认视为启用）；
- `resolve_business_agent()`：先取 `meta` 再判定——代码 Agent 若在库中被禁用则返回 `None`，与动态 Agent 口径统一；
- Orchestrator 唯一分发入口是 `resolve_business_agent`，返回 `None` 时安全兜底，禁用即真正阻断命中。

## 5. 自愈同步与「不复活禁用」

`validate_agents_sync()`（[core/bootstrap.py](../../backend/app/core/bootstrap.py)）启动时：凡「代码注册表有、库里缺」的 code Agent 自动 `create` 补登记（`display_name` 用 `description[:64]` 兜底）。该逻辑只补**缺失行**，不重置已有行的 `enabled`，故重启不会把已禁用的 Agent 改回启用。

## 6. 孤儿代码行容错与清理

「库里有 `source=code` 行、但代码注册表找不到」（代码已下线残留）时：

- **启动不再 `raise RuntimeError`**，改为 `logger.warning`，服务正常启动；
- 列表/详情把这行回显并标记 `stale=true`（在册代码 Agent `stale=false`）；
- 删除放行孤儿行：`DELETE /admin/agents/{name}` 中在册代码 Agent 仍禁删，孤儿行经 `AgentRepo.delete_by_name`（通用删除，调用方先校验）清理；
- 前端 `views/AgentList.vue`：来源列对 `stale` 显示红色「已失效」标签、隐藏启用开关、操作列仅保留「详情 + 删除」，删除确认文案区分「代码已下线，删除残留记录」；`types` 的 `AgentInfo` 加 `stale?: boolean`。

## 7. 验证

- `tests/test_dynamic_agents.py`：
  - 代码 Agent 列表/详情带出 `system_prompt` / `custom_tool_names` / `mcp_tools` / `registered_tools`（`knowledge_qa == ["memory","rag"]`）；
  - 禁用代码 Agent 被路由候选剔除、`resolve` 返回 `None`；
  - 动态 Agent 详情 `registered_tools` 推导正确；
  - 孤儿行：列表/详情 `stale=true`、删除放行、`validate_agents_sync` 遇孤儿不抛异常。
