"""流式工具执行链修复测试

回归点：hello_agents 的 SimpleAgent._execute_tool_call 用同步 tool.run(参数字典)
调用工具，而本项目自定义工具是 `async def run(**kwargs)`，导致协程未被 await、
工具根本不执行（工单/订单等不落库）。本文件验证项目级执行器：
- 协程型工具：绑定 kwargs、注入 user_id/session_id、真正执行；
- 同步型工具（MCP 包装工具）：按约定接收参数字典，仍可用；
- _stream_with_tools 端到端：产出 tool_call/tool_result 事件且工具确实执行；
- 同步工具硬超时：MCP 挂起时 _execute_tool 快速返回失败结果，不拖死请求；
- 协程工具超时：超时后 cancel 底层 Task，阻止工具在超时后继续写库；
- _astream 空闲看门狗：生产侧卡死时以一条 error 事件收尾，不永久挂起；
- 断开停止泵送：消费端关闭后，pump 线程不再继续生成。
"""
import asyncio
import threading
import time

from hello_agents.agents import SimpleAgent
from hello_agents.tools import Tool, ToolRegistry

from app.agents.orchestrator import _HISTORY_WINDOW, Orchestrator, _execute_tool, _stream_with_tools
from app.packs.customer_service.tools.ticket_tool import UpdateTicketTool
from app.tools.custom.base import BaseTool


class _RecordingAsyncTool(BaseTool):
    """记录每次被调用时收到的参数，用于断言 kwargs 绑定与上下文注入。"""

    name = "rec"
    description = "记录型异步工具"
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "客户 ID"},
            "content": {"type": "string", "description": "内容"},
        },
        "required": ["user_id", "content"],
        "additionalProperties": False,
    }
    calls: list = []

    async def run(self, user_id: str, content: str):
        _RecordingAsyncTool.calls.append((user_id, content))
        return {"ok": True, "echo": content}


class _AsyncRaiseTool(BaseTool):
    name = "boom"
    description = "总是失败的异步工具"
    parameters = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
        "additionalProperties": False,
    }

    async def run(self, x: str):
        raise RuntimeError("工具内部炸了")


class _SyncDictTool(Tool):
    """模拟 MCP 包装工具：同步 run(params: dict)。"""

    def __init__(self):
        super().__init__(name="sync", description="同步工具")

    def get_parameters(self):
        return []

    def run(self, params):
        return {"got": params}


class FakeLLM:
    def __init__(self, scripted):
        self.scripted = scripted
        self.i = 0

    def stream_invoke(self, messages, **kw):
        text = self.scripted[min(self.i, len(self.scripted) - 1)]
        self.i += 1
        for k in range(0, len(text), 8):
            yield text[k : k + 8]

    def invoke(self, messages, **kw):
        return self.scripted[-1]


def _make_agent(tools, scripted):
    reg = ToolRegistry()
    for t in tools:
        reg.register_tool(t)
    return SimpleAgent(name="t", llm=FakeLLM(scripted), tool_registry=reg)


def _reset_calls():
    _RecordingAsyncTool.calls.clear()


# ---------------- _execute_tool 单元 ----------------


def test_execute_tool_binds_kwargs_and_injects_user_id():
    _reset_calls()
    agent = _make_agent([_RecordingAsyncTool()], ["x"])
    res = _execute_tool(agent, "rec", {"content": "你好"}, {"user_id": "U9"}, None)
    # 仅给了 content，user_id 由会话上下文注入（工具声明了 user_id 必填）
    assert _RecordingAsyncTool.calls == [("U9", "你好")]
    assert "执行结果" in res


def test_execute_tool_respects_explicit_params_over_context():
    _reset_calls()
    agent = _make_agent([_RecordingAsyncTool()], ["x"])
    # 模型已给出 user_id 时不被上下文覆盖
    _execute_tool(agent, "rec", {"user_id": "M1", "content": "c"}, {"user_id": "CTX"}, None)
    assert _RecordingAsyncTool.calls == [("M1", "c")]


def test_execute_tool_sync_tool_receives_dict():
    agent = _make_agent([_SyncDictTool()], ["x"])
    res = _execute_tool(agent, "sync", {"a": "1"}, {}, None)
    assert "执行结果" in res and "'a': '1'" in res


class _HangingSyncTool(Tool):
    """模拟挂起的 MCP 工具：run 阻塞时间远大于 timeout，验证硬超时兜底。"""

    timeout = 0.15

    def __init__(self):
        super().__init__(name="hang", description="挂起的同步工具")

    def get_parameters(self):
        return []

    def run(self, params):
        time.sleep(0.6)
        return "迟到的结果"


def test_execute_tool_sync_timeout_returns_failure_without_waiting():
    agent = _make_agent([_HangingSyncTool()], ["x"])
    started = time.monotonic()
    res = _execute_tool(agent, "hang", {}, {}, None)
    elapsed = time.monotonic() - started
    # 超时后立即返回失败结果（交给模型继续生成），而不是等工具真正跑完
    assert "工具调用失败" in res and "超时" in res
    assert elapsed < 0.5


class _HangingAsyncTool(BaseTool):
    """模拟超时的异步自定义工具：run 的阻塞时间远大于 timeout。"""

    name = "slow"
    description = "挂起的异步工具"
    timeout = 0.1
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    async def run(self):
        await asyncio.sleep(5)
        return "迟到的结果"


async def test_execute_tool_async_timeout_cancels_underlying_task():
    """协程工具超时后必须 cancel 底层 Task，阻止其在超时后继续执行/写库。"""
    agent = _make_agent([_HangingAsyncTool()], ["x"])
    loop = asyncio.get_running_loop()
    started = time.monotonic()
    # _execute_tool 内部阻塞等待 future，必须放到工作线程；工具协程回投主循环执行，
    # 与生产链路（pump 线程 + 主 loop）一致
    res = await asyncio.to_thread(_execute_tool, agent, "slow", {}, {}, loop)
    elapsed = time.monotonic() - started
    assert "工具调用失败" in res and "超时" in res
    assert elapsed < 1.5


def test_execute_tool_missing_tool():
    agent = _make_agent([_RecordingAsyncTool()], ["x"])
    res = _execute_tool(agent, "no_such_tool", {}, {}, None)
    assert "未找到工具" in res


def test_execute_tool_async_exception_is_captured_not_raised():
    agent = _make_agent([_AsyncRaiseTool()], ["x"])
    res = _execute_tool(agent, "boom", {"x": "1"}, {}, None)
    assert "工具调用失败" in res and "工具内部炸了" in res


# ---------------- _stream_with_tools 端到端 ----------------


def test_stream_with_tools_executes_async_tool_end_to_end():
    _reset_calls()
    agent = _make_agent(
        [_RecordingAsyncTool()],
        ["明白了[TOOL_CALL:rec:content=投诉内容]", "已为您登记工单"],
    )
    events = list(
        _stream_with_tools(agent, "我要投诉", context={"user_id": "U1", "session_id": "S1"})
    )
    kinds = [e["type"] for e in events]
    assert "tool_call" in kinds and "tool_result" in kinds
    # 工具确实被执行，且 user_id 由上下文注入
    assert _RecordingAsyncTool.calls == [("U1", "投诉内容")]
    # 工具调用标记不应原样下发给前端
    streamed_text = "".join(e["data"]["content"] for e in events if e["type"] == "text")
    assert "[TOOL_CALL:" not in streamed_text
    assert "已为您登记工单" in streamed_text


def test_stream_with_tools_without_tool_call_unchanged():
    _reset_calls()
    agent = _make_agent([_RecordingAsyncTool()], ["这是一段普通回答"])
    events = list(_stream_with_tools(agent, "你好", context={}))
    assert _RecordingAsyncTool.calls == []
    text = "".join(e["data"]["content"] for e in events if e["type"] == "text")
    assert text == "这是一段普通回答"


# ---------------- _astream 空闲看门狗 ----------------


async def test_astream_idle_timeout_ends_with_error_event(monkeypatch):
    """生产侧卡死（不产出任何事件）时，空闲超时必须以一条 error 事件收尾。"""
    stop_event = threading.Event()

    def _hanging_stream(simple_agent, query, context=None, loop=None, max_tool_iterations=3, stop=None):
        stop_event.wait(5)  # 模拟 LLM/MCP 永久阻塞：在测试放行前不产出任何事件
        return
        yield  # pragma: no cover

    monkeypatch.setattr("app.agents.orchestrator._stream_with_tools", _hanging_stream)
    monkeypatch.setattr("app.agents.orchestrator._STREAM_IDLE_TIMEOUT", 0.2)

    try:
        events = [e async for e in Orchestrator._astream(object(), "hi")]
    finally:
        stop_event.set()

    assert events and events[-1]["type"] == "error"
    assert events[-1]["data"]["code"] == 5004
    assert "超时" in events[-1]["data"]["message"]


async def test_astream_stops_producer_after_consumer_close(monkeypatch):
    """消费端（客户端断开）关闭后，pump 线程必须停止，不再继续消耗 LLM token。"""
    stopped = threading.Event()

    def _endless_stream(simple_agent, query, context=None, loop=None, max_tool_iterations=3, stop=None):
        while stop is None or not stop.is_set():
            yield {"type": "text", "data": {"content": "x"}}
            time.sleep(0.005)
        stopped.set()

    monkeypatch.setattr("app.agents.orchestrator._stream_with_tools", _endless_stream)

    agen = Orchestrator._astream(object(), "hi")
    first = await agen.__anext__()
    assert first["type"] == "text"
    await agen.aclose()  # 模拟客户端断开：异步生成器被关闭

    for _ in range(100):
        if stopped.is_set():
            break
        await asyncio.sleep(0.02)
    assert stopped.is_set()


# ---------------- 跨轮历史注入（让 Agent 记住刚创建的工单号） ----------------


class _HistoryAgent:
    def __init__(self):
        self._history = []


def test_seed_history_injects_prior_turns_and_drops_current_user():
    agent = _HistoryAgent()
    history = [
        {"role": "user", "content": "我要投诉，快递太慢"},
        {"role": "assistant", "content": "已为您创建工单 TK-6AC4D659"},
        {"role": "user", "content": "订单号3444444"},  # 本轮输入，应被剔除
    ]
    Orchestrator._seed_history(agent, history)
    # 只注入上一轮，不重复当前用户消息
    assert [(m.role, m.content) for m in agent._history] == [
        ("user", "我要投诉，快递太慢"),
        ("assistant", "已为您创建工单 TK-6AC4D659"),
    ]


def test_seed_history_empty_or_none_is_noop():
    agent = _HistoryAgent()
    Orchestrator._seed_history(agent, None)
    Orchestrator._seed_history(agent, [])
    assert agent._history == []


def test_seed_history_caps_window():
    agent = _HistoryAgent()
    history = [{"role": "user", "content": f"m{i}"} for i in range(30)]
    # 末尾是当前轮 user，剔除后取最近 _HISTORY_WINDOW 条
    Orchestrator._seed_history(agent, history)
    assert len(agent._history) == _HISTORY_WINDOW
    assert agent._history[-1].content == "m28"


# ---------------- update_ticket 工具元数据 ----------------


def test_update_ticket_tool_signature():
    tool = UpdateTicketTool()
    assert tool.name == "update_ticket"
    declared = {p.name for p in tool.get_parameters()}
    assert {"ticket_id", "content"} <= declared
    assert "update_ticket" in tool.description and "ticket_id=" in tool.description
