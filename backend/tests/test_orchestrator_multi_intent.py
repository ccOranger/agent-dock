"""Orchestrator 多意图分发：串行执行、分块事件、阈值与 max_intents 语义（Router/业务 Agent 全 mock）"""
from types import SimpleNamespace

import pytest

from app.agents import orchestrator as orch_mod
from app.agents.orchestrator import Orchestrator
from app.core.config import settings


def _intent(name, conf=0.9, reason="r"):
    return {"agent_name": name, "confidence": conf, "reason": reason}


def _fallback_intent(name="knowledge_qa", conf=0.3):
    """路由判定“不贴合任何 Agent”时给出的兜底意图（置信度为提示词约定的固定低值）。"""
    return {"agent_name": name, "confidence": conf, "reason": "解析失败", "is_fallback": True}


class FakeRouter:
    def __init__(self, intents, fallback_agent=None):
        self.intents = intents
        self.fallback_agent = fallback_agent
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
            "fallback_agent": self.fallback_agent,
        }


class FakeCtx:
    def __init__(self):
        self.persisted: list[dict] = []

    async def build(self, user_id, query, rag_namespaces, collector=None):
        return query

    async def persist_after_run(self, **kw):
        self.persisted.append(kw)


def _make_orch(monkeypatch, intents, known=("knowledge_qa", "order_service", "complaint_handler"), fallback_agent=None):
    orch = Orchestrator()
    orch.router = FakeRouter(intents, fallback_agent)
    orch.ctx_engineer = FakeCtx()

    async def fake_business_agent(
        business_agent,
        user_id,
        query,
        session_id="",
        history=None,
        collector=None,
        intent_index=0,
        scene=None,
    ):
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
    events = [
        e
        async for e in orch.dispatch(
            user_id="u1", session_id="s1", query="q", history=history, **kw
        )
    ]
    # status 是前端进度提示（本文件里 _run_business_agent 被 mock，只剩 routing 一条），
    # 与业务事件契约正交，默认过滤以保持各用例既有的精确序列/下标断言
    return [e for e in events if e["type"] != "status"]


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
async def test_fallback_intent_is_answered_not_handed_over(monkeypatch):
    """兜底意图不能被“低置信转人工”吞掉，否则“你是谁”这类提问永远没人接。"""
    monkeypatch.setattr(settings, "router_fallback_answer", True)
    orch = _make_orch(monkeypatch, [_fallback_intent("knowledge_qa")])
    events = await _collect(orch)
    assert not any(e["type"] == "handover" for e in events)
    starts = [e["data"] for e in events if e["type"] == "agent_start"]
    assert [s["agent_name"] for s in starts] == ["knowledge_qa"]
    assert any(e["type"] == "text" for e in events)
    # 路由未命中具体业务：route 事件仍原样带出 is_fallback，前端/链路可辨
    assert events[0]["data"]["intents"][0]["is_fallback"] is True


@pytest.mark.asyncio
async def test_fallback_answer_disabled_falls_back_to_handover(monkeypatch):
    """ROUTER_FALLBACK_ANSWER=false 时恢复旧行为：兜底也按低置信转人工。"""
    monkeypatch.setattr(settings, "router_fallback_answer", False)
    orch = _make_orch(monkeypatch, [_fallback_intent("knowledge_qa")])
    events = await _collect(orch)
    assert [e["type"] for e in events] == ["route", "handover"]
    assert events[1]["data"]["reason"] == "low_confidence"


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
async def test_unavailable_intent_agent_falls_back_to_fallback(monkeypatch):
    """路由命中的 Agent 不可用（被禁用/已删）且路由带了兜底 Agent：回落应答，不再整轮 5006。"""
    orch = _make_orch(monkeypatch, [_intent("ghost_agent")], fallback_agent="knowledge_qa")
    events = await _collect(orch)
    assert not any(e["type"] == "error" for e in events)
    assert [e["data"]["agent_name"] for e in events if e["type"] == "agent_start"] == ["knowledge_qa"]
    assert [e["data"]["content"] for e in events if e["type"] == "text"] == ["<knowledge_qa>"]
    # 记忆归档记在实际上承接的 Agent 名下
    assert orch.ctx_engineer.persisted[0]["agent_name"] == "knowledge_qa"


@pytest.mark.asyncio
async def test_fallback_attempted_only_once_per_turn(monkeypatch):
    """多个不可用意图也只回落一次兜底，避免一轮里反复重复兜底回答。"""
    orch = _make_orch(
        monkeypatch,
        [_intent("ghost_a"), _intent("ghost_b", 0.8)],
        fallback_agent="knowledge_qa",
    )
    events = await _collect(orch, max_intents=2)
    assert [e["data"]["agent_name"] for e in events if e["type"] == "agent_start"] == ["knowledge_qa"]


@pytest.mark.asyncio
async def test_history_and_last_route_passed_to_router(monkeypatch):
    orch = _make_orch(monkeypatch, [_intent("order_service")])
    history = [{"role": "assistant", "content": "已发货"}]
    await _collect(orch, history=history, last_route_to="order_service")
    call = orch.router.calls[0]
    assert call["history"] == history
    assert call["last_route_to"] == "order_service"


# ===== rag namespace 解析：单库 + 显式多库合并去重 / 全部库开关 =====
class _NsAgent:
    def __init__(self, rag_namespace=None, rag_namespaces=None, search_all=False):
        self.rag_namespace = rag_namespace
        self.rag_namespaces = rag_namespaces or []
        self.search_all_knowledge_bases = search_all


@pytest.mark.asyncio
async def test_resolve_rag_namespaces_merges_single_and_multi():
    agent = _NsAgent(rag_namespace="cs_order", rag_namespaces=["cs_after_sales", "cs_order", "cs_default"])
    result = await Orchestrator._resolve_rag_namespaces(agent)
    # 自身默认库前置，与显式多库合并去重，保持声明顺序
    assert result == ["cs_order", "cs_after_sales", "cs_default"]


@pytest.mark.asyncio
async def test_resolve_rag_namespaces_only_multi():
    agent = _NsAgent(rag_namespace=None, rag_namespaces=["cs_a", "cs_b"])
    assert await Orchestrator._resolve_rag_namespaces(agent) == ["cs_a", "cs_b"]


@pytest.mark.asyncio
async def test_resolve_rag_namespaces_empty():
    assert await Orchestrator._resolve_rag_namespaces(_NsAgent()) == []


@pytest.mark.asyncio
async def test_resolve_rag_namespaces_search_all_prepends_declared(monkeypatch):
    from app.models import repositories as repo_mod

    bases = [
        SimpleNamespace(namespace="cs_x", is_active=True),
        SimpleNamespace(namespace="cs_order", is_active=True),
        SimpleNamespace(namespace="cs_off", is_active=False),
    ]

    async def fake_list_all():
        return bases

    monkeypatch.setattr(repo_mod.KnowledgeBaseRepo, "list_all", fake_list_all)
    agent = _NsAgent(rag_namespace="cs_order", rag_namespaces=["cs_y"], search_all=True)
    result = await Orchestrator._resolve_rag_namespaces(agent)
    # 自身默认库与显式多库前置，再补齐其余启用库（禁用库 cs_off 不入）
    assert result == ["cs_order", "cs_y", "cs_x"]

