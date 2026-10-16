"""Orchestrator 多意图分发：串行执行、分块事件、阈值与 max_intents 语义（Router/业务 Agent 全 mock）"""
import pytest

from app.agents import orchestrator as orch_mod
from app.agents.orchestrator import Orchestrator


def _intent(name, conf=0.9, reason="r"):
    return {"agent_name": name, "confidence": conf, "reason": reason}


class FakeRouter:
    def __init__(self, intents):
        self.intents = intents
        self.calls: list[dict] = []

    async def route(self, query, history=None, last_route_to=None, collector=None):
        self.calls.append(
            {"query": query, "history": history, "last_route_to": last_route_to, "collector": collector}
        )
        primary = self.intents[0]
        return {
            "intents": self.intents,
            "agent_name": primary["agent_name"],
            "confidence": primary["confidence"],
            "reason": primary["reason"],
        }


class FakeCtx:
    def __init__(self):
        self.persisted: list[dict] = []

    async def build(self, user_id, query, rag_namespaces, collector=None):
        return query

    async def persist_after_run(self, **kw):
        self.persisted.append(kw)


def _make_orch(monkeypatch, intents, known=("knowledge_qa", "order_service", "complaint_handler")):
    orch = Orchestrator()
    orch.router = FakeRouter(intents)
    orch.ctx_engineer = FakeCtx()

    async def fake_business_agent(business_agent, user_id, query, session_id="", history=None, collector=None, intent_index=0):
        yield {"type": "text", "data": {"content": f"<{business_agent.name}>"}}

    orch._run_business_agent = fake_business_agent

    async def fake_resolve(name, user_id, session_id=None):
        if name not in known:
            return None
        return SimpleNamedAgent(name)

    monkeypatch.setattr(orch_mod, "resolve_business_agent", fake_resolve)
    # 阈值固定，避免跟随 .env 漂移
    monkeypatch.setattr(orch_mod, "CONFIDENCE_THRESHOLD", 0.5)
    return orch


class SimpleNamedAgent:
    def __init__(self, name):
        self.name = name


async def _collect(orch, **kw):
    history = kw.pop("history", [])
    return [
        e
        async for e in orch.dispatch(
            user_id="u1", session_id="s1", query="q", history=history, **kw
        )
    ]


@pytest.mark.asyncio
async def test_multi_intent_serial_execution_and_block_events(monkeypatch):
    orch = _make_orch(monkeypatch, [_intent("order_service"), _intent("knowledge_qa", 0.7)])
    events = await _collect(orch)
    types = [e["type"] for e in events]

    assert types == [
        "route",
        "agent_start",
        "text",
        "agent_end",
        "text",  # 分块分隔
        "agent_start",
        "text",
        "agent_end",
    ]
    starts = [e["data"] for e in events if e["type"] == "agent_start"]
    assert [s["agent_name"] for s in starts] == ["order_service", "knowledge_qa"]
    assert [s["intent_index"] for s in starts] == [0, 1]
    assert all(s["intent_total"] == 2 for s in starts)
    ends = [e["data"] for e in events if e["type"] == "agent_end"]
    assert [e["intent_index"] for e in ends] == [0, 1]
    # 分隔符独立成块，位于两个分支之间
    assert events[4]["data"]["content"] == orch_mod._INTENT_SEPARATOR
    # route 事件保留旧字段并带上意图清单
    route = events[0]["data"]
    assert route["agent_name"] == "order_service"
    assert [i["agent_name"] for i in route["intents"]] == ["order_service", "knowledge_qa"]
    assert route["intent_total"] == 2
    # 一轮只归档一次，记在主意图名下
    assert orch.ctx_engineer.persisted[0]["agent_name"] == "order_service"


@pytest.mark.asyncio
async def test_secondary_low_confidence_dropped_but_primary_answered(monkeypatch):
    orch = _make_orch(monkeypatch, [_intent("order_service", 0.9), _intent("knowledge_qa", 0.2)])
    events = await _collect(orch)
    assert not any(e["type"] == "handover" for e in events)
    assert [e["data"]["agent_name"] for e in events if e["type"] == "agent_start"] == ["order_service"]
    assert orch.router.calls[0]["history"] == []


@pytest.mark.asyncio
async def test_primary_low_confidence_hands_over(monkeypatch):
    orch = _make_orch(monkeypatch, [_intent("order_service", 0.4), _intent("knowledge_qa", 0.3)])
    events = await _collect(orch)
    assert [e["type"] for e in events] == ["route", "handover"]
    assert events[1]["data"] == {"reason": "low_confidence", "confidence": 0.4}


@pytest.mark.asyncio
async def test_max_intents_caps_branches(monkeypatch):
    orch = _make_orch(
        monkeypatch,
        [_intent("order_service"), _intent("knowledge_qa", 0.8), _intent("complaint_handler", 0.7)],
    )
    events = await _collect(orch, max_intents=1)
    assert [e["data"]["agent_name"] for e in events if e["type"] == "agent_start"] == ["order_service"]
    assert events[0]["data"]["intent_total"] == 1

    # 默认取配置值（2）
    orch2 = _make_orch(
        monkeypatch,
        [_intent("order_service"), _intent("knowledge_qa", 0.8), _intent("complaint_handler", 0.7)],
    )
    events2 = await _collect(orch2)
    assert len([e for e in events2 if e["type"] == "agent_start"]) == 2


@pytest.mark.asyncio
async def test_unregistered_secondary_skipped_primary_ok(monkeypatch):
    orch = _make_orch(monkeypatch, [_intent("order_service"), _intent("ghost_agent", 0.8)])
    events = await _collect(orch)
    assert not any(e["type"] == "error" for e in events)
    assert [e["data"]["agent_name"] for e in events if e["type"] == "agent_start"] == ["order_service"]


@pytest.mark.asyncio
async def test_all_unregistered_reports_5006(monkeypatch):
    orch = _make_orch(monkeypatch, [_intent("ghost_agent")])
    events = await _collect(orch)
    assert events[-1]["type"] == "error"
    assert events[-1]["data"]["code"] == 5006
    assert orch.ctx_engineer.persisted == []


@pytest.mark.asyncio
async def test_history_and_last_route_passed_to_router(monkeypatch):
    orch = _make_orch(monkeypatch, [_intent("order_service")])
    history = [{"role": "assistant", "content": "已发货"}]
    await _collect(orch, history=history, last_route_to="order_service")
    call = orch.router.calls[0]
    assert call["history"] == history
    assert call["last_route_to"] == "order_service"
