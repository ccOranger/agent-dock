# U05 · stdio 型本地 MCP 演示服务与固定数据

> 状态：已落地 ｜ 影响面：MCP 加载器、tools 表配置、跨环境路径解析、启动编码、演示数据

## 1. 背景与问题

Spec 假定 MCP 是外部 HTTP/SSE 服务（`mcp_logistics` 配 `endpoint`）。本地演示没有稳定的外部 MCP 域名，旧库还遗留假域名 `http://mcp-*:9000/sse`，连接会超时。需要一个 **零端口、跨机器可移植** 的 MCP 方案，并让进程内自定义工具与 MCP 服务共用同一份数据。

方案：物流/退款实现为 **本地 FastMCP 服务**（stdio 子进程），由 `MCPTool` 拉起，无需端口/域名；固定数据集中在单一真相 `mock_data.py`。

## 2. 服务与工具（backend/mcp_servers/）

| 服务 | 文件 | 展开后暴露给模型的工具名 |
|------|------|--------------------------|
| `mcp_logistics` | `logistics_server.py` | `mcp_logistics_query_logistics`、`mcp_logistics_query_order_logistics_status` |
| `mcp_refund` | `refund_server.py` | `mcp_refund_submit_refund`、`mcp_refund_query_refund` |

- 各服务 `FastMCP("...")` + `@mcp.tool()`，`__main__` 走 `mcp.run(transport="stdio")`；
- 脚本以绝对路径被拉起时把 `backend` 根加入 `sys.path`，`import app.tools.custom.mock_data`（链路各 `__init__` 为空、无副作用）；
- 进程内自定义工具（`query_order`/`query_logistics`/`submit_refund` 等，`tools/custom/*.py`）与 MCP 服务 **共用同一份 `mock_data`**（内存态固定数据）。

## 3. tools 表配置（stdio）

`scripts/seed_data.py` 写入（并对旧数据 upsert 覆盖），`config` 结构：

```json
{
  "transport": "stdio",
  "command": "<venv python，可留空>",
  "args": ["<mcp_servers/xxx_server.py 路径>"],
  "timeout_seconds": 20
}
```

旧库遗留的假 `endpoint`（`http://mcp-*:9000/sse`）会导致连接超时；重跑 `seed_data.py` 或 `rebuild_and_seed.py` 即覆盖为 stdio。

## 4. 跨环境路径/命令解析（tools/mcp_loader.py，方案 A）

DB 只存相对路径 / 留空解释器即可跨 Windows/Linux/容器运行，加载时按当前主机重解析：

- `_resolve_stdio_command`：
  - 留空 → 用运行进程 `sys.executable`；
  - 裸命令名（python/uv/npx/…）→ 交 PATH 解析；Windows 上 `npx/npm/pnpm` 等实为 `.cmd`，本机存在 `<name>.cmd` 时补后缀（DB 仍存裸名，Linux 保持原样）；
  - 绝对路径解释器 → 仅本机存在才用，否则回退 `sys.executable`（兼容旧库写死的异机路径）。
- `_resolve_stdio_arg`：含 `mcp_servers` 段的参数取其后相对片段按 `_BACKEND_ROOT` 重定位；选项 token（`-` 开头）原样保留。
- `_build_mcp_server_source`：stdio 返回命令列表；sse/http 返回 `{transport,url,headers,auth}` 配置字典。

## 5. MCPTool 展开（expandable）

0.2.9 `MCPTool` 默认 `expandable=False`，会被当作单个「元工具」（参数只有 action/tool_name/arguments），模型看不到真实函数。注册前置 `True`：

```python
tool = MCPTool(name=cfg["name"], server_command=server_source)
tool.expandable = True
registry.register_tool(tool)   # 展开为每个远端工具的独立 MCPWrappedTool
```

## 6. 阻塞隔离：工具发现不能冻住事件循环

`MCPTool.__init__` 的 `_discover_tools()` 是 **无超时的阻塞网络调用**。若在事件循环线程执行会冻住整个 FastAPI。加载器逐服务在 **独立守护线程** 构造并注册，`join(timeout_seconds)` 硬超时（默认 15s）：

```python
def _register_one(...):
    worker = threading.Thread(target=_work, daemon=True)
    worker.start(); worker.join(timeout_seconds)
    if worker.is_alive(): 跳过该工具        # 超时只影响这一个服务
```

上层 `BaseBusinessAgent.build(mcp_configs)` 本身也被 Orchestrator 放到 `asyncio.to_thread` 执行（build 内会构造 MCPTool）；MCP 配置则 **预先在主 event loop** 通过 `fetch_mcp_configs()` 取好，使 build 不再触碰 DB（见 [U09](09-async-tool-execution.md)）。

## 7. Windows GBK 控制台 emoji 陷阱

hello_agents 的 MCP 客户端用裸 `print` 输出 emoji（📝/🔗/✅），在 Windows GBK 控制台抛 `UnicodeEncodeError` 并被工具发现逻辑 **静默吞掉**，导致 MCP 工具列表为空。`app/main.py` 启动时把 `stdout/stderr` 切到 UTF-8：

```python
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass
```

手动跑脚本验证时先 `$env:PYTHONIOENCODING="utf-8"`。

## 8. 可演示的固定订单

| 订单号 | 状态 | 备注 |
|--------|------|------|
| SO20240001 | 已发货 | 有物流（顺丰，运输中） |
| SO20240002 | 已支付 | 未发货，无物流；可改备注/申请退款 |
| SO20240003 | 已签收 | 有物流（中通） |
| SO20240004 | 处理中 | 可申请退款 |
| SO20240005 | 已取消 | 已有退款单 RF20240001；退款会被拒 |
| SO20240006 | 已发货 | 有物流（京东） |

> 退款提交仅内存累加，进程重启回到初始数据；改单字段白名单限于 `address`/`remark`。

## 9. 单独自检某 MCP 服务（不经后端）

```powershell
cd d:\project\smart-cs\backend; $env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -c "import asyncio,sys; from hello_agents.protocols.mcp.client import MCPClient;\nasync def m():\n    async with MCPClient([sys.executable,'mcp_servers/logistics_server.py']) as c:\n        print(await c.list_tools())\nasyncio.run(m())"
```

## 10. 官方演示型 stdio MCP

除自研服务外，也可直接挂官方/第三方 stdio MCP（如 `uv run mcp-server-*`、`npx @modelcontextprotocol/server-*`）：command 填裸命令名（走 PATH + Windows `.cmd` 补全），args 填脚本/包参数，`transport=stdio` 即可，无需改代码。

## 11. 验证

`tests/test_mcp_stdio.py`、`test_mcp_mock_data.py`、`test_admin_mcp_tools.py`。
