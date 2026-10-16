# U09 · 异步自定义工具执行机制（协程工具真正落地）

> 状态：已落地 ｜ 影响面：`agents/orchestrator.py` 工具执行链、`tools/custom/base.py` 适配层、自定义工具写法约定

## 1. 背景与问题

公开版 `hello-agents 0.2.x` 的 `SimpleAgent` 只按**同步** `tool.run(参数字典)` 调用工具，且这套调用只为 MCP 包装工具设计。本项目的自定义工具（订单/物流/退款/工单）采用**「类属性声明 + `async def run(**kwargs)`」**写法，直接沿用框架的同步调用会出现两种坏结果：

- 调用 `tool.run(dict)` 返回一个**未 await 的协程对象**，被当字符串拼进上下文 → 模型看到 `coroutine object...`，工具**根本没执行、不落库**；
- 或参数形态不匹配直接 `TypeError`。

同时，框架的 `stream_run` **只做一次 LLM 流式调用、完全没有工具循环**，模型会把 `[TOOL_CALL: ...]` 和 `<result>{...}</result>` 一起「演」出来当作真实结果（假数据）。

## 2. 适配基类 `BaseTool`（tools/custom/base.py）

对框架 `Tool` 做一层薄适配，让子类只需按直觉写法声明：

- `name / description / parameters`（JSON-schema dict）作为**类属性**；
- `__init__` 从类属性读取并初始化父类；
- `get_parameters()` 把 `parameters.properties` 转成框架要的 `ToolParameter` 列表（`required` 命中即置必选）；
- 子类实现 **`async def run(**kwargs)`** 覆盖基类的占位 `run`（占位 `run` 抛 `NotImplementedError`）。

工具体样例（`order_tool.py`）：

```python
class QueryOrderTool(BaseTool):
    name = "query_order"
    description = "根据订单号查询订单状态。"
    parameters = {"type": "object", "properties": {"order_id": {"type": "string"}},
                  "required": ["order_id"], "additionalProperties": False}

    async def run(self, order_id: str):
        order = mock_data.get_order(order_id)
        return {"ok": True, "data": order} if order else {"ok": False, "error": "订单不存在"}
```

## 3. 项目级工具执行器 `_execute_tool`（orchestrator.py）

替代框架的同步 `_execute_tool_call`，统一两种工具形态：

1. **协程型自定义工具**（`inspect.iscoroutinefunction(tool.run)`）：
   - 绑定 `**kwargs` 后 **回投到主事件循环** 执行 —— `asyncio.run_coroutine_threadsafe(tool.run(**args), loop)` + `future.result(timeout)`；
   - 原因：asyncpg 连接池绑定在主 loop 上，**不能在工作线程里另起 loop**，否则 `Future attached to a different loop`；
   - 超时：默认 `_DEFAULT_TOOL_TIMEOUT=30s`，工具可用 `timeout` 类属性覆盖。
2. **同步工具（MCP 包装工具）**：按框架约定 `tool.run(args_dict)`。

**会话上下文自动注入**：`user_id / session_id` 是模型不该也不会填的内部参数。执行器读取 `tool.get_parameters()` 得到工具**显式声明**的参数名，仅当「工具声明了该参数、模型没给、且会话上下文里有值」时补进去。因此工单类工具（`create_ticket` 等声明了 `user_id/session_id`）能拿到当前会话身份落库，模型无需填写。

异常统一兜底：任何工具执行失败都被捕获并返回 `❌ 工具调用失败：...` 文本（不抛出中断整轮），同时 `logger.exception` 记栈。

## 4. 真正的工具循环 `_stream_with_tools`（orchestrator.py）

对齐 `SimpleAgent.run` 语义自实现多轮工具循环（`max_tool_iterations=3`），并严格区分「下发内容」与「内部推理」：

- 系统提示 = `agent._get_enhanced_system_prompt()` + 注入的跨轮历史（[U06](06-tickets-cross-round-memory.md) `_seed_history`）+ 当前输入；
- 无工具能力时：纯流式逐块下发 `text`（UX 不变）；
- 有工具时：
  - 中间轮（含 `[TOOL_CALL...]`）的原始文本**不下发**，用 `_safe_prefix_len` 截断标记前缀避免漏出，只发 `tool_call` / `tool_result` 事件；
  - 执行结果以「工具执行结果：\n…」回喂模型，进入下一轮；
  - 最终轮（无新工具调用）文本仍逐块流式下发；
  - 结束后把本轮 user/assistant 追加进 `agent._history`。

## 5. 同步生成器 → 异步流的桥接 `_astream`（orchestrator.py）

`_stream_with_tools` 是**同步生成器**（框架 LLM 客户端为同步），而对话接口是 SSE 异步流。桥接方式：

- 在主 loop `asyncio.get_running_loop()` 捕获 `loop`；
- 起一条 `daemon` 线程跑同步生成器，逐事件 `loop.call_soon_threadsafe(queue.put_nowait, event)` 回投到 `asyncio.Queue`；
- 主协程 `await queue.get()` 消费并 `yield`，遇 `_SENTINEL` 收尾；
- 字符串事件归一化为 `{"type":"text","data":{"content":...}}`，保证下游 `dispatch()`/SSE 契约一致；
- 线程内异常回投 `error` 事件（code 5001），绝不静默。

> 工具执行（协程型）在这条工作线程里被触发，但通过 `run_coroutine_threadsafe(..., loop)` **回到主 loop 执行**，因此 DB 连接池可用、`create_ticket` 等能真正落库。

## 6. 与链路追踪的关系

每次 `tool.call` 由链路采集器记 `start_ms / duration_ms / status / attributes`（见 [U04](04-request-tracing.md)）；工具异常会被标记，且即便 `TRACE_SAMPLE_RATE<1`，带工具异常的轮次也永远全量保留。

## 7. 验证

- `tests/test_custom_tools.py`：`BaseTool.get_parameters` 转换、协程工具 `run(**kwargs)` 真实执行、上下文注入；
- `tests/test_tool_execution.py`：`_execute_tool` 对协程/同步两种形态、超时与异常兜底；
- `tests/test_tickets.py` / `tests/test_orchestrator_multi_intent.py`：`create_ticket` 经异步执行链真正落库、跨轮 `update_ticket` 引用工单号。

## 8. 已知边界

- 自定义工具必须在**主事件循环**可见的依赖上工作（DB 走 `run_coroutine_threadsafe` 回投）；若工具内部又自建新 loop 会破坏连接池绑定。
- 工具循环上限 `max_tool_iterations=3`，防止模型反复触发工具死循环；超限则以最后一次文本收尾。
- 同步 MCP 包装工具仍在**工作线程**内执行（框架本就同步），其网络阻塞不受主 loop 超时控制，由 MCP 客户端自身的 `timeout_seconds` 约束（[U05](05-stdio-mcp-demo.md)）。
