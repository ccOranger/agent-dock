# U10 · 自定义工具管理与 MCP 服务描述

> 状态：已落地 ｜ 影响面：tools 表、动态 Agent 工具装配、管理后台、启动/编辑校验

## 1. 背景与问题

代码侧已开发的一批进程内 `BaseTool`（`query_order` / `create_ticket` 等）此前只能被**代码 Agent** 在 `build_tools` 里硬编码注册；**动态 Agent** 无法复用它们（能力边界被限制在 prompt + memory/rag + MCP）。运营希望：在后台看到代码里已有哪些自定义工具、按需登记、并勾选绑定给动态 Agent，无需改代码。

设计原则：**工具实现永远来自代码**，后台只做「登记入口」——把散落在 `custom/*.py` 的 `BaseTool` 子类集中列举，暴露 `name/description/parameters`（JSON Schema），供扫描展示、勾选登记到 `tools` 表（`type=custom`），再由动态 Agent 按名绑定。

## 2. 代码目录（tools/custom_catalog.py）

```python
CATALOG: dict[str, type[BaseTool]] = { cls.name: cls for cls in (QueryOrderTool, ...) }
def exists(name) -> bool          # 名称是否在目录中
def build(name) -> BaseTool|None  # 无参实例化（user_id/session_id 由 orchestrator 执行时注入）
def list_specs() -> list[dict]    # {name, description, parameters}，供后台扫描
```

- 采用**显式列举**而非反射扫描整包：避免误收 `BaseTool` 基类、`mock_data` 等非工具模块；登记顺序/内容可控，新增工具时在此加一行。
- 键即类属性 `name`，全局唯一，与 `tools.name` / MCP 名共用唯一约束。

## 3. 运行时装配（tools/custom_loader.py）

对称于 `mcp_loader.register_mcp_tools`，但简单得多——自定义工具是进程内 Python 对象，无网络、无跨事件循环问题，纯同步即可：

```python
def register_custom_tools(registry, names) -> list[str]
    # 逐个 exists→build→registry.register_tool；不在目录的告警跳过
```

动态 Agent 的 `build_tools` 在装配完 memory/rag 后，按 `custom_tool_names` 调用此函数把已登记工具挂上。

## 4. 数据落点：tools 表（type=custom）

登记信息存入 `tools` 表，`config`（JSONB）承载描述等元数据，**无需新增列/迁移**：

| 字段 | 说明 |
|------|------|
| `name` | 与代码目录一致的工具名 |
| `type` | 固定 `custom` |
| `schema_` | 工具参数 JSON Schema（登记时从目录快照） |
| `enabled` | 后台启停开关 |
| `config.description` | 覆盖显示描述（可选，仅展示用，不改工具实现） |

## 5. 管理后台 API（api/v1/admin.py）

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/admin/custom-tools` | staff | 扫描代码目录列出全部工具，标注登记/启用状态与覆盖描述 |
| POST | `/admin/custom-tools` | admin | 把目录中的工具登记到 tools 表（目录存在性 + 重名/MCP 占用校验） |
| PATCH | `/admin/custom-tools/{name}/toggle` | admin | 启用/禁用 |
| DELETE | `/admin/custom-tools/{name}` | admin | 取消登记（被动态 Agent 引用时拒绝，避免悬空） |

绑定校验 `_validate_custom_tool_bindings`：动态 Agent 创建/更新时，先做**纯内存**的目录存在性检查（不触库，避免误开连接池），再查一次库确认「已登记且启用」。

## 6. MCP 服务描述字段

MCP 服务管理（tools 表 type=mcp）新增 `description`，用于记录该服务的作用：

- 存于 `tools.config`（JSONB），**无需迁移**；
- 请求体 `MCPServerCreate / MCPServerUpdate` 加 `description: str | None`（`max_length=512`）；
- `_build_mcp_config` 仅在有值时写入、`_config_to_body` 还原、`list_mcp_servers` 顶层回传；空描述不落库、还原为 `None`。

## 7. 前端（admin-frontend）

- `views/CustomToolsView.vue`：扫描列表、勾选登记、启停、取消登记、覆盖描述；
- `views/McpServersView.vue`：表格新增「描述」列、表单新增描述 textarea。

## 8. 验证

- `tests/test_custom_tools.py`：目录扫描、登记/重复登记/未知名拒绝、被引用拒删、绑定校验。
- `tests/test_admin_mcp_tools.py`：MCP 描述往返（有值落库/回显、空值不落库）。
