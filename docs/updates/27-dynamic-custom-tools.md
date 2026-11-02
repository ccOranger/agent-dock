# U27 · 自定义工具动态加载（后台在线写源码，免重启生效）

> 状态：**已落地**。对应「工具能否动态加载 / 动态添加」的方案 A 实施。
> 关联：[U03 动态 Agent](03-dynamic-agents.md)、[U09 异步自定义工具执行](09-async-tool-execution.md)、
> [U10 自定义工具管理](10-custom-tool-management.md)、[U05 stdio MCP](05-stdio-mcp-demo.md)

## 1. 背景与问题

U10 落地的自定义工具是**半动态**的：登记/启停/绑定在 DB 与后台完成，但**实现必须写在
`app/tools/custom/` 并手工补一行 `_TOOL_CLASSES`，重启后端才生效**。私有化交付现场
「客户想加一个查会员等级的小工具」就得改代码发版，节奏完全被开发侧牵住。

对比同期能力：MCP 工具早已是「后台配 → 下一轮对话可用」（`tools` 表 + 运行时装配 +
发现缓存），因为 MCP 的能力载体是外部服务，配置只是数据。自定义工具的差距就在于
**实现本身被固化在代码里**。

本次把自定义工具拆成两个来源，统一到同一个登记/绑定/执行链路：

| 来源 | 实现位置 | 新增/改动是否要重启 | 后台能力 |
|------|----------|-------------------|----------|
| `code`（存量，行为不变） | `app/tools/custom/*.py` + `custom_catalog` | 要 | 登记 / 启停 |
| `dynamic`（本次新增） | `tools.config.code`（DB 里的 Python 源码） | **不要** | 新建 / 写源码 / 校验 / 试运行 / 启停 / 删除 |

## 2. 设计要点

### 2.1 复用既有契约，不加表不加列

工具仍是 `tools` 表 `type='custom'` 的一行，源码放 `config`（JSONB）：

```json
{
  "source": "dynamic",
  "code": "class query_membership(BaseTool):\n    ...",
  "description": "后台展示用覆盖描述（可选）",
  "code_sha256": "1a2b3c4d5e6f7a8b",
  "code_updated_at": "2026-11-02T01:00:00+00:00"
}
```

`schema` 列在保存时由服务端**编译后回填**真实 JSON Schema（并把代码里的 description
一并存进去），因此后台列表、Agent 绑定候选、链路追踪的工具名统计全部无需改动。

零 DDL：`tools.type` 的 CHECK 约束仍是 `('mcp','custom','builtin')`，不新增枚举值。

### 2.2 两来源统一装配，代码目录优先

`custom_loader.register_custom_tools(registry, names, configs)` 是动态 Agent 的唯一入口：

1. 名字命中 `custom_catalog`（代码目录）→ 直接实例化，**存量工具零回归**；
2. 否则从预取的 `configs[name]["code"]` 编译 → 实例化；
3. 两者都不是 / 编译失败 → 记 warning 跳过，**绝不阻断整个 Agent 构建**（与 MCP
   「单个服务超时只影响它自己」同一容错口径）。

同名时以代码目录为准：后台即便误建了与线上代码同名的动态工具，也不会覆盖线上实现。

### 2.3 DB 访问必须在主循环预取（沿用 MCP 的成熟模式）

`build()` 在 `asyncio.to_thread` 的工作线程里执行，不能碰绑定主循环的 asyncpg 连接。
所以和 `fetch_mcp_configs()` 完全对称，新增 `fetch_dynamic_configs()`，在
`orchestrator._run_business_agent()` 里**先 await 预取源码，再传给 build**：

```python
simple_agent = await asyncio.to_thread(business_agent.build, mcp_configs, custom_configs)
```

`BaseBusinessAgent.build()` 因此多了第二个可选参数 `custom_configs`，落到
`self.custom_configs` 供 `build_tools()` 读取；预取异常被兜住降级为空配置（本轮只装
配代码目录工具），对话不中断。

### 2.4 安全：保存关口 + 加载关口 + 执行兜底 三层

进程内 `exec` 用户代码做不到绝对隔离，因此按「只信任有 `custom-tools` 写权限的后台
操作员」的前提做纵深防御：

1. **AST 静态校验**（`app/tools/tool_security.py`，保存与加载都跑一遍，不执行代码）
   - import 白名单：`json/datetime/re/math/collections/dataclasses/uuid/logging/asyncio…`
     等纯计算标准库 + `httpx/numpy/pandas`；其余一律拒绝（`os`、`sys`、`subprocess`、
     `shutil`、`pathlib`、`socket`、`importlib`、`app.*` 内部模块、相对导入）；
   - 危险内建调用黑名单：`open/exec/eval/compile/__import__/getattr/globals/locals/
     vars/input/breakpoint/exit/quit/memoryview`；
   - dunder 属性黑名单：`__globals__/__class__/__subclasses__/__mro__/__dict__/
     __code__/__reduce__/__bases__/__builtins__/__closure__/__func__/__self__…`
     （`__init__/__name__/__doc__` 等常规用法放行）；
   - 语法合法 + 源码 ≤ 400 行。
2. **受限 builtins 执行环境**（`tool_code_loader._safe_builtins()`）：黑名单内建在
   exec 的命名空间里直接缺席，AST 漏网也调不到。两层口径有意不同：`print` 只在
   命名空间层缺席（AST 不拦，避免拦掉调试写法），`getattr/setattr` 只在 AST 层拦
   显式调用、命名空间里保留——解释器的属性协议与 `dataclasses` 内部需要它们，
   删掉会让合法写法莫名崩溃。
   - **`__import__` 不能一删了之**：它是所有 `import` 语句的实际入口，剔掉后连
     `import json` 都会报 `ImportError: __import__ not found`。做法是**替换为白名单
     守卫版** `_guarded_import()`，运行期复用 `tool_security.is_module_allowed()` 再判一次
     （与 AST 层同一份规则，避免「静态放过、运行期也放过」的偏差），并拒绝相对导入。
     因此 `DENIED_MODULES` 是硬黑名单（优先于白名单前缀匹配），把 `asyncio.subprocess`
     这类「合法包里的危险子模块」堵死；`create_subprocess_exec/shell`、
     `run_coroutine_threadsafe` 同时进 `FORBIDDEN_ATTR_NAMES`。
3. **执行侧兜底**（复用 U09 已有链路）：`orchestrator._execute_tool()` 读实例的
   `timeout` 属性做硬超时并 `future.cancel()`，异常包成错误文本回给模型。动态工具
   可以写 `timeout = 10`，但被钳制在上限 120s 内。生产路径是回投主循环
   （`run_coroutine_threadsafe` + `future.result(timeout)`）；`loop=None` 的直调分支
   （单测/脚本）此前会 `asyncio.run()` 无限等工具自己跑完，现已统一为
   `asyncio.run(asyncio.wait_for(...))`，两条路径超时口径与文案一致。

写操作全部走 RBAC `custom-tools: write` + `AuditRepo`（`dynamic_create` /
`dynamic_update` / `delete` 三类 action）。

### 2.5 编译缓存：指纹命中零编译，坏代码不覆盖好版本

对齐 `mcp_loader` 的已验证模式（`tool_code_loader.py`）：

- `_cache[name] = (source_sha256, compiled_class, monotonic_ts)`，`threading.Lock` 保护；
- 指纹一致 → 直接复用同一个类（每轮对话都建 Agent，不可能每轮都编译）；
- 源码变更 → 指纹变化自动重新编译，**下一轮对话即生效**；
- 编译/校验失败 → **不写缓存**，已缓存的可用版本继续服务（改崩了不影响线上）；
- TTL 3600s 仅作为兜底回收（删除工具时另有 `invalidate(name)` 主动释放）；
- 工具实例仍每轮 `cls()` 新建（与代码目录工具一致），实例态不跨轮共享。

### 2.6 用户代码写法：BaseTool 由系统注入

被禁止 `import app.*`，所以基类直接注入 exec 命名空间，后台模板因此极简：

```python
class query_membership(BaseTool):
    name = "query_membership"                      # 必须与登记名一致
    description = "按手机号查询会员等级。仅在客户询问会员等级/权益时调用。"
    parameters = {                                  # JSON Schema，模型据此填参
        "type": "object",
        "properties": {"phone": {"type": "string", "description": "手机号"}},
        "required": ["phone"],
        "additionalProperties": False,
    }
    # timeout = 10                                 # 可选，覆盖默认 30s

    async def run(self, phone: str):
        return {"ok": True, "data": {"level": LEVELS.get(phone, "普通卡")}}
```

`user_id` / `session_id` 若在 `parameters` 里声明，执行时由系统按 U09 的注入机制自动
补齐，模型不需要也不该填。

## 3. 接口契约（`/api/v1/admin`）

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/custom-tools` | staff | 合并返回两来源，新增 `source` / `has_code` / `code_updated_at` |
| POST | `/custom-tools` | custom-tools:write | （原）登记代码目录工具 |
| POST | `/custom-tools/dynamic` | custom-tools:write | 新建动态工具：安全校验 + 编译通过才落库 |
| GET | `/custom-tools/{name}/code` | staff | 取源码供编辑器回显（非动态工具 404） |
| PUT | `/custom-tools/{name}/code` | custom-tools:write | 整份替换源码并重编译（代码目录工具 404） |
| POST | `/custom-tools/validate` | custom-tools:write | 只校验不入库，返回错误明细 + 解析出的元信息 |
| POST | `/custom-tools/test-run` | custom-tools:write | 用给定入参真实执行一次（默认 10s 上限 60s） |
| PATCH | `/custom-tools/{name}/toggle` | custom-tools:write | 启停（两来源通用） |
| DELETE | `/custom-tools/{name}` | custom-tools:write | 取消登记 / 删除动态工具 + 释放编译缓存 |

安全/编译失败统一返回 `400`（`code=1001`，`message` 为逐条明细）；重名返回 `409`。

`_validate_custom_tool_bindings()` 放宽为「代码目录 **或** 带可用源码的动态工具」，
绑定时就会真正编译一次，避免把坏源码的工具绑到 Agent 上才在运行时静默失败。

## 4. 后台交互（admin-frontend）

`CustomToolsView.vue` 在同一张表里管两来源：

- 新增「来源」列（代码 / 动态）与「源码更新」列（`YYYY-MM-DD HH:mm:ss`）；
- 「新建动态工具」→ 抽屉式表单：名称、覆盖显示描述、Python 源码多行编辑器
  （等宽字体、Tab 键补 4 空格避免 Python 缩进混用、一键填入示例模板）；
- 底部三键：**校验**（红/绿条显示逐条错误码与解析出的参数）、**试运行**（弹 JSON 入参
  → 真实执行 → 回显结果/异常）、**保存**；
- 动态行操作：编辑代码 / 删除；代码行操作保持原样（登记 / 取消登记）；
- `AgentList.vue` 绑定候选与提示文案同步为「两来源均可绑定」。

## 5. 关键文件

| 文件 | 变更 |
|------|------|
| `backend/app/tools/tool_security.py` | 新增：AST 白名单/黑名单校验 + 元信息静态提取 |
| `backend/app/tools/tool_code_loader.py` | 新增：受限命名空间编译、类校验、指纹缓存、`invalidate/preview` |
| `backend/app/tools/custom_loader.py` | 新增 `fetch_dynamic_configs()`；`register_custom_tools()` 支持两来源 |
| `backend/app/tools/custom_catalog.py` | 新增 `spec_of()`；文档说明与动态来源的分工 |
| `backend/app/agents/base_business_agent.py` | `build(mcp_configs, custom_configs)`，注入 `self.custom_configs` |
| `backend/app/agents/business/dynamic_agent.py` | `build_tools()` 传入动态源码配置 |
| `backend/app/agents/orchestrator.py` | 主循环预取源码（异常降级）并传给 `build()` |
| `backend/app/schemas/api.py` | `CustomToolDynamicCreate / CodeUpdate / ValidateRequest / TestRunRequest` |
| `backend/app/api/v1/admin.py` | 5 个新端点 + 列表合并 + 绑定校验放宽 + 删除释放缓存 |
| `admin-frontend/src/{types,api}/index.ts`、`views/CustomToolsView.vue`、`views/AgentList.vue` | 类型与接口、编辑器与试运行 UI |
| `backend/tests/test_dynamic_custom_tools.py` | 新增 54 个用例（安全/编译/缓存/装配/接口/超时） |

## 6. 验证

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_custom_tools.py tests/test_custom_tools.py -q
.\.venv\Scripts\python.exe -m pytest tests -q          # 全量回归
cd ../admin-frontend ; npm run build                   # vue-tsc + vite
```

手工链路（已于 2026-11-02 真机跑通，后台 UI 新建 + 接口回读双轨）：后台新建动态工具 → 校验/试运行 →
动态 Agent 绑定 → 对话里问该问题 → 链路详情 `available_tools` 出现工具名且 `tool.call` span 带
args/result → 改一版源码保存 → **不重启、同一会话**再问一次，结果即新版。

真机验证的三个前提（踩过的坑，写清楚以免下次误判为“动态加载没生效”）：

1. **动态 Agent 必须绑到一个多 Agent 的路由场景**。`default` 场景是 `router_mode=direct` 的单 Agent
   直连（`candidate_count=1`、`router_skipped=true`），新 Agent 不参与候选也永远不会被路由到；
   验证要用 `customer_service` 这类 `router_mode=llm` 的场景，并在对话请求里带 `scene_code`。
   绑定入口是 `PATCH /admin/scenes/{id}` 的 `agent_ids`（建 Agent 时不选场景）。
2. **对话必须用客户身份**。拿 staff token 调 `/chat/*` 会被拒（`403`，`code=2003`）；不带 token 则取决于
   场景是否开匿名访问。建议用客户账号登录后再发。
3. **改崩的两种形态要分开看**：① AST/编译不通过的代码在 `PUT /code` 入处就 400（不落库、不覆盖缓存），
   线上继续跑旧版；② AST 合法但运行期抛异常的代码能保存，此时靠 `_execute_tool` 把异常包成错误文本
   回给模型（对话不 500，但见 §7 “链路里看不出工具失败”）。

## 7. 已知边界与后续

- **不是完整沙箱**：进程内 `exec` 无法阻断 CPU 死循环、超大内存分配等；因此源码只有
  `custom-tools: write` 权限者可改，且务必保留审计。若将来要开放给低信任角色，应改为
  方案 B（HTTP 型工具）或独立进程执行，而不是放宽这里的白名单。
- **不能 import 项目内部模块**：动态工具只能用白名单标准库 + `httpx` 自行访问内部
  接口；需要复用内核能力（知识库检索、工单写入）的场景，仍应走代码目录工具。
- **无依赖安装**：源码只能依赖镜像里已有的包；`requirements` 之外的库不可用。
- **链路里看不出工具失败**（既有设计，非本次引入）：`_execute_tool` 把异常包成错误文本后
  `tool_result` 事件带的 `error` 仍为 `None`，所以 `tool_error_count` 不计数、`tool.call` span
  的 `status` 仍是 `ok`（见 `orchestrator.py` 内对应注释）。排查动态工具时要看 span 的
  `attributes.result` 文本里有没有 ❌，而不能只看“工具错误数=0”。采样策略也会因此把这类轮次
  当正常应答按 `TRACE_SAMPLE_RATE` 丢弃。
- **执行超时已覆盖但不覆盖编译期死循环**：`ast.literal_eval` 与编译本身是有限工作，
  源码上限 400 行已约束；真正的运行期死循环由 `timeout` 兜。
- **`asyncio` 在白名单内**：动态工具可以自己起协程（`asyncio.sleep`、并发 `httpx`），
  但拿不到宿主事件循环句柄（`get_running_loop` 之类只读，且 `run_coroutine_threadsafe`
  已被属性黑名单堵住），因此不能反向调度到主循环里去抢资源或绕过超时。
- **未做版本历史**：`config.code` 整份覆盖，回滚需从审计日志/备份库取旧值；如需
  灰度与回滚，下一步加 `tools.config.code_history`（保留最近 N 份）。
- **下一步（方案 B/C 未实施）**：HTTP webhook 型工具（`type='http'`）与 MCP 配置保存
  即时清缓存（当前靠指纹变化绕过缓存，最迟下一轮对话生效）。

## 8. 上线注意事项（随 v0.3.0 发布）

- **零 DDL、零新配置项**：源码存在既有的 `tools.config` JSON 列里，`scripts/migrations/` 没有新增
  迁移，升级按 [docs/deployment.md](../deployment.md) §5 走「备份 → `git pull` → `migrate --check`（应报无待执行）
  → `up -d --build`」即可；`backend/.env` 无需新增变量。回滚同理：直接回退镜像，不需要恢复库结构。
- **已有动态工具会跨版本存活**：`tools` 行与 `config.code` 是数据，重启/换镜像不会丢；新容器起来后
  第一轮对话按源码重新编译（缓存是进程内的，重启即失效，属预期）。
- **权限面比之前宽**：该能力等同于给 `custom-tools: write` 持有者一个「在后端容器内执行受限 Python」的
  通道。上线后建议核对一遍拥有该写权限的角色；白名单含 `httpx`，所以工具能否访问内网其他服务，
  取决于容器的出网策略——需要收敛就在 compose 层面限制 backend 的可达网段。
- **升级后先做一次试运行**：后台「自定义工具」→ 任一动态工具 → 校验 + 试运行，确认安全校验与执行链路在
  生产环境依赖齐全（个别白名单库若未装进镜像，会在试运行阶段就暴露，而不是等到客户对话）。
