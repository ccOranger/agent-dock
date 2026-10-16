# U03 · 动态 Agent 混合注册与路由实时化

> 状态：已落地 ｜ 影响面：agents 表结构、Agent 注册表、路由、管理后台、启动校验

## 1. 背景与问题

Spec v1.3 的「业务 Agent = 一个 Python 类」要求新增业务必须改代码、注册表加一行、重启。运营/客服希望 **在后台直接配置一个新业务 Agent**（Prompt + 模型 + 知识库 + MCP 工具），无需发版即可被路由命中。

方案：**代码 Agent 与动态 Agent 混合注册** —— 保留代码托管 Agent 的能力边界（进程内自定义工具），同时支持 DB 驱动的动态 Agent；二者共享同一 `BaseBusinessAgent` 抽象与同一条路由/编排链路。

## 2. agents 表扩展（source 分流）

`AgentMeta` 新增列（见 [init_db.sql](../../scripts/init_db.sql) 与 `models/entities.py`）：

| 列 | 说明 |
|----|------|
| `source` | `code` \| `dynamic`；code 由 Python 类托管、后台仅能开关；dynamic 完全由 DB 配置、可增删改 |
| `system_prompt` | dynamic 必填 |
| `llm_model` | 空则回退默认 `LLM_MODEL_ID` |
| `rag_namespace` | 绑定的知识库命名空间 |
| `search_all_knowledge_bases` | 检索覆盖全部启用知识库 |
| `mcp_tool_names` | JSONB 数组，仅存 MCP 服务名（配置见 tools 表 type=mcp） |

## 3. 注册表解析（agents/registry.py）

```python
AGENT_REGISTRY: dict[str, type[BaseBusinessAgent]]   # 代码 Agent（knowledge/order/complaint）

async def list_dynamic_agents(enabled_only=True)     # DB source=dynamic 摘要
async def list_effective_agents()                    # 代码 + 启用中动态（路由提示词数据源）
async def resolve_business_agent(name, user_id, session_id)
    # 优先代码注册表 → cls(user_id, session_id)
    # 其次 DB dynamic 且 enabled → DynamicBusinessAgent(meta=..., user_id, session_id)
    # 都没有 → None
```

`resolve_business_agent` 是 Orchestrator 每轮把意图名解析为实例的唯一入口，代码与动态走同一 `build()` 流程。

## 4. DynamicBusinessAgent（agents/business/dynamic_agent.py）

以 detached 的 `AgentMeta` 行填充类属性，能力边界 = prompt + memory/rag + 绑定的 MCP 工具：

```python
def build_tools(self, registry):
    registry.register_tool(get_memory_tool(self.user_id))   # 动态 Agent 必挂 memory 工具
    if self.rag_namespace:
        registry.register_tool(get_rag_tool(self.rag_namespace))
```

> 无进程内自定义 Python 工具（如 `query_order`）；需要这类硬编码能力时仍应写代码 Agent。

## 5. 路由实时化

路由每轮调用 `list_effective_agents()` 实时拼接清单（[U02](02-multi-intent-routing.md)），故后台新增/启用/禁用 Agent **立即生效**，无需重启。

## 6. 启动校验（core/bootstrap.py::validate_agents_sync）

只校验 **代码 Agent**：DB 中 `source=code` 的行必须都能在 `AGENT_REGISTRY` 找到，否则抛错；代码注册但 DB 缺失的自动补登记。`source=dynamic` 不参与校验。

## 7. 管理后台 API（api/v1/admin.py）

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/admin/agents` | admin | 代码 + 动态合并列表（代码 Agent 的 MCP 绑定只读） |
| GET | `/admin/agents/{name}` | admin | 详情 |
| POST | `/admin/agents` | admin | 创建动态 Agent（重名校验、MCP 绑定校验、namespace 校验） |
| PUT | `/admin/agents/{name}` | admin | 编辑（仅 dynamic 可改，字段级更新） |
| DELETE | `/admin/agents/{name}` | admin | 删除（代码 Agent 不可删，只能禁用） |
| PATCH | `/admin/agents/{name}/toggle` | admin | 启用/禁用（代码/动态通用） |

创建/更新约束：
- 名称 `^[A-Za-z0-9_]+$`，不得与代码 Agent 或已有 Agent 重名（`Conflict`）；
- 绑定的 MCP 工具必须存在且启用（`_validate_mcp_bindings`）；
- `rag_namespace` 必须已在 knowledge_bases 登记；
- 全部写操作落 `audit_logs`。

请求/响应体见 `schemas/api.py::DynamicAgentCreate / DynamicAgentUpdate`。

## 8. MCP 服务管理（同一 admin.py）

动态 Agent 的 MCP 绑定依赖后台维护的 MCP 服务（tools 表 type=mcp）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/mcp-servers` | 列表 + 反查被哪些 Agent 绑定（含代码 Agent 类属性） |
| POST | `/admin/mcp-servers` | 新建（stdio / sse / http） |
| PUT | `/admin/mcp-servers/{id}` | 更新（config 合并回填） |
| DELETE | `/admin/mcp-servers/{id}` | 删除（被动态 Agent 引用时拒绝，避免悬空） |
| POST | `/admin/mcp-servers/test` | 连通性探测（列远端工具，不落库） |
| GET | `/admin/mcp-servers/{id}/tools` | 按已存配置连接并返回全部工具 |

`config` 归一化结构由 `_build_mcp_config` / `_config_to_body` 处理（stdio→command/args；sse/http→endpoint/headers/auth）。

## 9. 前端（admin-frontend）

- `views/AgentList.vue`：列表、创建/编辑动态 Agent、启停开关；
- `views/McpServersView.vue`：MCP 服务 CRUD 与连通测试。

## 10. 验证

- `tests/test_dynamic_agents.py`：动态 Agent 创建→被 `resolve_business_agent` 解析→路由清单包含它；代码 Agent 不可编辑/删除。
- `tests/test_admin_mcp_tools.py`：MCP CRUD 与绑定校验。
