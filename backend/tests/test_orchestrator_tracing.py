"""Orchestrator 链路采集集成测试：真实 TraceCollector + mock Router/LLM 流

验证「一次请求经过哪些 Agent / MCP / 工具」确实被收拢成行，且采集失败不会
影响对话事件下发（collector 为 None 时也必须照常工作）。
"""
import pytest

from app.agents import orchestrator as orch_mod
from app.agents.orchestrator import Orchestrator
from app.core.config import settings
from app.core.tracing import (
    OUTCOME_ANSWERED,
    OUTCOME_ERROR,
    OUTCOME_HANDOVER,
    SPAN_BUILD,
    SPAN_MEMORY,
    SPAN_PERSIST,
    SPAN_RAG,
    SPAN_RESOLVE,
    SPAN_ROUTER,
    SPAN_RUN,
    SPAN_TOOL,
    TraceCollector,
)
from app.tools import mcp_loader as mcp_loader_mod


class FakeRouter:
    def __init__(self, intents):
        self.intents = list(intents)

    async def route(self, query, history=None, last_route_to=None, collector=None):
        primary = self.intents[0]
        if collector:
            # 与真实 RouterAgent 一致：写 router.llm span 与模型维度
            collector.add(SPAN_ROUTER, "router-model", started_ms=collector.now_ms(), model="router-model")
            collector.add_unique("llm_models", "router-model")
        return {
            "intents": self.intents,
            "agent_name": primary["agent_name"],
            "confidence": primary["confidence"],
            "reason": primary["reason"],
        }


class FakeCtx:
    async def build(self, user_id, query, rag_namespaces, collector=None, runtime=None):
        if collector:
            collector.memory_hit_count = 2
            collector.add(SPAN_MEMORY, "memory_search", hit_count=2)
            for ns in rag_namespaces:
                collector.add_unique("rag_namespaces", ns)
                collector.add(SPAN_RAG, ns, namespace=ns, chunk_count=3)
                collector.rag_chunk_count += 3
        return query

    async def persist_after_run(self, **kw):
        collector = kw.get("collector")
        if collector:
            collector.add(SPAN_PERSIST, "memory_add", importance=0.7)


class FakeRegistry:
    def __init__(self, tools):
        self._tools = tools

    def list_tools(self):
        return list(self._tools)


class FakeAgent:
    """结构对齐 BaseBusinessAgent 中被 dispatch 读取的属性。"""

    def __init__(self, name, mcp_tools=(), rag_namespace="cs_default", llm_model="biz-model", tools=()):
        self.name = name
        self.mcp_tools = list(mcp_tools)
        self.rag_namespace = rag_namespace
        self.search_all_knowledge_bases = False
        self.llm_model = llm_model
        self._tools = list(tools)

    def build(self, mcp_configs=None):
        return type("S", (), {"tool_registry": FakeRegistry(self._tools), "enable_tool_calling": False})()


def _intent(name, conf=0.9):
    return {"agent_name": name, "confidence": conf, "reason": "r"}


def _make_orch(monkeypatch, intents, agents, known=None, stream_events=None):
    orch = Orchestrator()
    orch.router = FakeRouter(intents)
    orch.ctx_engineer = FakeCtx()

    known_names = set(known if known is not None else agents)

    async def fake_resolve(name, user_id, session_id=None):
        return agents.get(name) if name in known_names else None

    async def fake_astream(simple_agent, query, context=None):
        for event in stream_events or [{"type": "text", "data": {"content": "答案"}}]:
            yield event

    async def fake_fetch_mcp_configs(names):
        # 链路测试不依赖 DB：MCP 配置缺失时按“未注册”处理，只验证 span 记录
        return {}

    # orchestrator 在 _run_business_agent 内部才 import fetch_mcp_configs，需补丁源头模块
    monkeypatch.setattr(mcp_loader_mod, "fetch_mcp_configs", fake_fetch_mcp_configs)
    monkeypatch.setattr(orch_mod, "resolve_business_agent", fake_resolve)
    monkeypatch.setattr(orch_mod, "CONFIDENCE_THRESHOLD", 0.5)
    monkeypatch.setattr(Orchestrator, "_astream", staticmethod(fake_astream))
    # 代码注册表内的判为 code，其余只可能来自 DB -> dynamic
    monkeypatch.setattr(
        orch_mod, "AGENT_REGISTRY", {"knowledge_qa": object(), "order_service": object()}, raising=False
    )
    return orch


async def _run(orch, collector, include_status: bool = False):
    events = [
        e
        async for e in orch.dispatch(
            user_id="u1", session_id="s1", query="查一下订单并解释退货政策", history=[], collector=collector
        )
    ]
    # status 是前端进度提示，与业务事件契约正交，默认过滤掉不影响各用例的精确序列断言
    if not include_status:
        events = [e for e in events if e["type"] != "status"]
    return events


def _of_type(collector, type_):
    return [s for s in collector.spans if s["type"] == type_]


def _intent(name, conf=0.9):
    return {"agent_name": name, "confidence": conf, "reason": "r"}


@pytest.mark.asyncio
async def test_single_intent_full_timeline(monkeypatch):
    tool_events = [
        {"type": "tool_call", "data": {"id": "c1", "name": "order_query", "args": {"order_no": "A1"}}},
        {"type": "tool_result", "data": {"id": "c1", "name": "order_query", "result": "已发货", "error": None}},
        {"type": "text", "data": {"content": "您的订单已发货"}},
    ]
    orch = _make_orch(
        monkeypatch,
        [_intent("order_service")],
        {"order_service": FakeAgent("order_service", mcp_tools=["erp-mcp"], tools=["order_query", "search_knowledge"])},
        stream_events=tool_events,
    )
    collector = TraceCollector(trace_id="tid-1", user_id="u1", session_id="s1")
    events = await _run(orch, collector)

    assert events[-1]["type"] == "agent_end"
    row = collector.to_row()
    assert row["trace_id"] == "tid-1"
    assert row["outcome"] == OUTCOME_ANSWERED
    assert row["agent_names"] == ["order_service"]
    assert row["agent_sources"] == ["code"]
    assert row["mcp_server_names"] == ["erp-mcp"]
    assert row["tool_names"] == ["order_query"]
    assert row["rag_namespaces"] == ["cs_default"]
    assert row["llm_models"] == ["router-model", "biz-model"]
    assert (row["tool_call_count"], row["memory_hit_count"], row["rag_chunk_count"]) == (1, 2, 3)
    assert row["response_chars"] == 0  # 行级回复字数由 ChatService 在落库前回填
    assert _of_type(collector, SPAN_RUN)[0]["attributes"]["response_chars"] == len("您的订单已发货")
    assert row["ttft_ms"] is not None
    # 整轮耗时由 ChatService 在助手消息落库后 mark_finished（流中途才知道收尾）
    assert row["total_latency_ms"] is None
    assert collector.should_persist() is True

    # 时间线顺序即执行顺序：路由 → 意图 → 解析 → 构建 → 工具 → 运行
    seq = [(s["type"], s["name"]) for s in collector.spans]
    types_in_order = [t for t, _ in seq]
    assert types_in_order.index(SPAN_ROUTER) < types_in_order.index(SPAN_RESOLVE)
    assert types_in_order.index(SPAN_RESOLVE) < types_in_order.index(SPAN_BUILD)
    assert types_in_order.index(SPAN_BUILD) < types_in_order.index(SPAN_TOOL)
    assert types_in_order.index(SPAN_TOOL) < types_in_order.index(SPAN_RUN)

    resolve = _of_type(collector, SPAN_RESOLVE)[0]
    assert resolve["attributes"]["declared_mcp"] == ["erp-mcp"]
    assert resolve["attributes"]["rag_namespace"] == "cs_default"

    build = _of_type(collector, SPAN_BUILD)[0]
    assert build["attributes"]["llm_model"] == "biz-model"
    assert build["attributes"]["mcp_connected"] == []  # 未真正连接（mock），仅声明
    assert build["attributes"]["available_tools"] == ["order_query", "search_knowledge"]

    tool = _of_type(collector, SPAN_TOOL)[0]
    assert tool["status"] == "ok"
    assert tool["attributes"]["agent_name"] == "order_service"
    assert "已发货" in tool["attributes"]["result"]
    assert tool["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_multi_intent_records_both_branches(monkeypatch):
    orch = _make_orch(
        monkeypatch,
        [_intent("order_service"), _intent("vip_assistant", 0.8)],
        {
            "order_service": FakeAgent("order_service"),
            "vip_assistant": FakeAgent("vip_assistant", rag_namespace="cs_vip"),
        },
    )
    collector = TraceCollector(trace_id="tid-2", user_id="u1", session_id="s1")
    await _run(orch, collector)

    row = collector.to_row()
    assert row["agent_names"] == ["order_service", "vip_assistant"]
    # order_service 在代码注册表内 -> code；vip_assistant 只能来自 DB -> dynamic
    assert row["agent_sources"] == ["code", "dynamic"]
    assert row["intent_count"] == 2 and row["confidence"] == 0.9
    assert row["rag_namespaces"] == ["cs_default", "cs_vip"]
    assert [s["attributes"]["intent_index"] for s in _of_type(collector, SPAN_RUN)] == [0, 1]
    assert [s["name"] for s in _of_type(collector, "route.intent")] == ["order_service", "vip_assistant"]


@pytest.mark.asyncio
async def test_handover_recorded_and_always_persisted(monkeypatch):
    orch = _make_orch(monkeypatch, [_intent("order_service", 0.4)], {"order_service": FakeAgent("order_service")})
    collector = TraceCollector(trace_id="tid-3", user_id="u1", session_id="s1")
    events = await _run(orch, collector)

    assert events[-1]["type"] == "handover"
    row = collector.to_row()
    assert row["outcome"] == OUTCOME_HANDOVER
    assert row["handover_reason"] == "low_confidence"
    assert row["agent_names"] == []  # 未进入任何业务 Agent
    assert row["total_latency_ms"] is not None
    assert collector.should_persist() is True


@pytest.mark.asyncio
async def test_unregistered_agent_marks_error_span_and_outcome(monkeypatch):
    orch = _make_orch(monkeypatch, [_intent("ghost_agent")], {})
    collector = TraceCollector(trace_id="tid-4", user_id="u1", session_id="s1")
    events = await _run(orch, collector)

    assert events[-1]["data"]["code"] == 5006
    resolve = _of_type(collector, SPAN_RESOLVE)[0]
    assert resolve["status"] == "error"
    assert resolve["attributes"]["error"] == "agent_not_registered"
    row = collector.to_row()
    assert row["outcome"] == OUTCOME_ERROR and row["error_code"] == 5006
    assert row["agent_names"] == []
    assert collector.should_persist() is True


@pytest.mark.asyncio
async def test_tool_error_counted_and_persisted_under_zero_sample(monkeypatch):
    monkeypatch.setattr(settings, "trace_sample_rate", 0.0)
    orch = _make_orch(
        monkeypatch,
        [_intent("order_service")],
        {"order_service": FakeAgent("order_service")},
        stream_events=[
            {"type": "tool_call", "data": {"id": "c1", "name": "refund_apply", "args": {}}},
            {
                "type": "tool_result",
                "data": {
                    "id": "c1",
                    "name": "refund_apply",
                    "result": None,
                    "error": "工具执行失败",
                },
            },
            {"type": "text", "data": {"content": "稍后再试"}},
        ],
    )
    collector = TraceCollector(trace_id="tid-5", user_id="u1", session_id="s1")
    await _run(orch, collector)

    row = collector.to_row()
    assert (row["tool_call_count"], row["tool_error_count"]) == (1, 1)
    assert _of_type(collector, SPAN_TOOL)[0]["status"] == "error"
    # 采样率为 0 时，带工具异常的轮次仍必须保留
    assert collector.should_persist() is True


@pytest.mark.asyncio
async def test_payload_clipped_in_agent_run(monkeypatch):
    monkeypatch.setattr(settings, "trace_store_payload", True)
    monkeypatch.setattr(settings, "trace_payload_max_chars", 10)
    orch = _make_orch(
        monkeypatch,
        [_intent("order_service")],
        {"order_service": FakeAgent("order_service")},
        stream_events=[
            {"type": "tool_call", "data": {"id": "c1", "name": "order_query", "args": {"note": "x" * 40}}},
            {"type": "tool_result", "data": {"id": "c1", "name": "order_query", "result": "y" * 40, "error": None}},
            {"type": "text", "data": {"content": "ok"}},
        ],
    )
    collector = TraceCollector(trace_id="tid-6", user_id="u1", session_id="s1")
    await _run(orch, collector)

    attrs = _of_type(collector, SPAN_TOOL)[0]["attributes"]
    assert len(attrs["args"]) <= 10 + len('{"note":"') + 2
    assert attrs["args_truncated"] is True
    assert attrs["result"] == "y" * 10 and attrs["result_truncated"] is True


@pytest.mark.asyncio
async def test_dispatch_works_without_collector(monkeypatch):
    """采集是旁路：不传 collector 时事件契约必须完全不变。"""
    orch = _make_orch(monkeypatch, [_intent("order_service")], {"order_service": FakeAgent("order_service")})
    events = await _run(orch, collector=None)
    assert [e["type"] for e in events] == ["route", "agent_start", "text", "agent_end"]


@pytest.mark.asyncio
async def test_status_events_report_progress_stages(monkeypatch):
    """status 事件按真实阶段下发：路由→构建准备→生成，且先于 route 事件。"""
    orch = _make_orch(monkeypatch, [_intent("order_service")], {"order_service": FakeAgent("order_service")})
    events = await _run(orch, collector=None, include_status=True)

    assert events[0]["type"] == "status" and events[0]["data"]["stage"] == "routing"
    stages = [e["data"]["stage"] for e in events if e["type"] == "status"]
    assert stages == ["routing", "preparing", "generating"]
    # 进度事件不改变业务事件序列（过滤 status 后契约不变）
    assert [e["type"] for e in events if e["type"] != "status"] == [
        "route", "agent_start", "text", "agent_end",
    ]
