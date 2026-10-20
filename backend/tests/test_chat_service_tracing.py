"""ChatService 链路落库测试：trace_id 贯通、done 事件回带、写入失败与异常降级

Repo 全 mock，只验证「采集 → 落库 → 下发」这条旁路的契约，不触真实 DB。
"""
import json
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.tracing import OUTCOME_ANSWERED, OUTCOME_ERROR
from app.services import chat_service as cs_mod
from app.services.chat_service import ChatService


class FakeSession:
    def __init__(self):
        self.id = "11111111-1111-1111-1111-111111111111"
        self.user_id = "22222222-2222-2222-2222-222222222222"
        self.last_route_to = "order_service"
        # 与真实 ORM 默认值保持一致：新建会话标题是占位值，等待首条消息自动补全
        self.title = "新对话"


def _install_fakes(monkeypatch, *, events, inserted: list, raiser: Exception | None = None, outcome=OUTCOME_ANSWERED):
    sess = FakeSession()

    class FakeDB:
        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSessionFactory:
        def __call__(self):
            return FakeDB()

    async def fake_get_or_create(user_id, session_id, db=None):
        return sess

    async def fake_add(session_id, role, content, routed_to=None, db=None):
        seq = "3" if role == "user" else "4"
        return SimpleNamespace(id=f"{seq}" * 32, content=content, role=role)

    async def fake_list_recent(session_id, limit=50, db=None):
        return []

    async def fake_insert(row):
        if raiser:
            raise raiser
        inserted.append(row)

    monkeypatch.setattr(cs_mod, "SessionLocal", FakeSessionFactory())
    monkeypatch.setattr(cs_mod.SessionRepo, "get_or_create", fake_get_or_create)
    monkeypatch.setattr(cs_mod.MessageRepo, "add", fake_add)
    monkeypatch.setattr(cs_mod.MessageRepo, "list_recent", fake_list_recent)
    monkeypatch.setattr(cs_mod.TraceRepo, "insert", fake_insert)

    service = ChatService()

    async def fake_dispatch(self, **kw):
        collector = kw["collector"]
        # 模拟 Orchestrator 的最小行为：记意图 + 一个工具调用 + 文本 + 结局
        collector.record_intents([{"agent_name": "order_service", "confidence": 0.9, "reason": "r"}], 2)
        collector.add_unique("agent_names", "order_service")
        key = collector.record_tool_call(name="order_query", args={"no": "A1"}, agent_name="order_service")
        collector.finish_tool_call(key, result="已发货")
        if outcome:
            collector.record_outcome(outcome)
        for event in events:
            yield event

    service.orchestrator.dispatch = lambda **kw: fake_dispatch(None, **kw).__aiter__()
    return service, sess


def _payload(sse_text: str, event: str) -> dict | None:
    for block in sse_text.split("\n\n"):
        if not block.strip():
            continue
        lines = block.split("\n")
        if lines[0].strip() == f"event: {event}":
            return json.loads(lines[1][len("data: ") :])
    return None


async def _drain(aiter) -> list[str]:
    return [chunk async for chunk in aiter]


@pytest.mark.asyncio
async def test_stream_persists_trace_and_returns_trace_id(monkeypatch):
    inserted: list[dict] = []
    service, sess = _install_fakes(
        monkeypatch,
        events=[
            {
                "type": "route",
                "data": {
                    "agent_name": "order_service",
                    "confidence": 0.9,
                    "reason": "r",
                    "intents": [],
                    "intent_total": 1,
                },
            },
            {"type": "text", "data": {"content": "您的订单"}},
            {"type": "text", "data": {"content": "已发货"}},
        ],
        inserted=inserted,
    )

    chunks = await _drain(
        service.stream(user_id=sess.user_id, session_id=None, message="到哪了", trace_id="abcd1234abcd1234")
    )
    done = _payload(chunks[-1], "done")

    assert len(inserted) == 1
    row = inserted[0]
    assert row["trace_id"] == "abcd1234abcd1234"
    assert row["session_id"] == sess.id and row["user_id"] == sess.user_id
    assert row["user_message_id"] == "3" * 32 and row["assistant_message_id"] == "4" * 32
    assert row["agent_names"] == ["order_service"]
    assert row["tool_call_count"] == 1
    assert row["response_chars"] == len("您的订单已发货")
    assert row["total_latency_ms"] is not None
    assert row["outcome"] == "answered"
    # 前端拿到 trace_id 才能跳到链路详情
    assert done == {"message_id": "4" * 32, "trace_id": "abcd1234abcd1234"}


@pytest.mark.asyncio
async def test_stream_generates_trace_id_when_absent(monkeypatch):
    inserted: list[dict] = []
    service, sess = _install_fakes(monkeypatch, events=[{"type": "text", "data": {"content": "x"}}], inserted=inserted)
    chunks = await _drain(service.stream(user_id=sess.user_id, session_id=None, message="x"))
    assert len(inserted) == 1
    assert len(inserted[0]["trace_id"]) == 16
    assert _payload(chunks[-1], "done")["trace_id"] == inserted[0]["trace_id"]


@pytest.mark.asyncio
async def test_trace_write_failure_does_not_break_stream(monkeypatch):
    """链路是旁路：DB 写失败必须只记日志，对话照常收尾。"""
    inserted: list[dict] = []
    service, sess = _install_fakes(
        monkeypatch,
        events=[{"type": "text", "data": {"content": "正常回答"}}],
        inserted=inserted,
        raiser=RuntimeError("request_traces 表不存在"),
    )
    chunks = await _drain(service.stream(user_id=sess.user_id, session_id=None, message="x"))
    assert inserted == []
    assert _payload(chunks[-1], "done")["message_id"] == "4" * 32


@pytest.mark.asyncio
async def test_dispatch_exception_persists_error_trace(monkeypatch):
    inserted: list[dict] = []
    service, sess = _install_fakes(
        monkeypatch,
        events=[{"type": "text", "data": {"content": "半句"}}],
        inserted=inserted,
    )

    boom = RuntimeError("LLM 连接超时")

    async def exploding_dispatch(**kw):
        collector = kw["collector"]
        collector.add_unique("agent_names", "order_service")
        yield {"type": "text", "data": {"content": "半句"}}
        raise boom

    service.orchestrator.dispatch = lambda **kw: exploding_dispatch(**kw).__aiter__()

    with pytest.raises(RuntimeError):
        await _drain(service.stream(user_id=sess.user_id, session_id=None, message="x", trace_id="t-err"))

    assert len(inserted) == 1
    row = inserted[0]
    assert row["outcome"] == OUTCOME_ERROR
    assert "LLM 连接超时" in row["error_message"]
    assert row["assistant_message_id"] is None  # 异常轮次没有助手消息
    assert row["total_latency_ms"] is not None


@pytest.mark.asyncio
async def test_sampled_out_round_is_not_written(monkeypatch):
    """采样率 0 + 正常应答轮：不写库（异常/转人工轮在采集器单测里已验证为全量保留）。"""
    monkeypatch.setattr(settings, "trace_sample_rate", 0.0)
    inserted: list[dict] = []
    service, sess = _install_fakes(
        monkeypatch,
        events=[{"type": "text", "data": {"content": "x"}}],
        inserted=inserted,
    )
    await _drain(service.stream(user_id=sess.user_id, session_id=None, message="x"))
    assert inserted == []


@pytest.mark.asyncio
async def test_client_disconnect_does_not_record_error_trace(monkeypatch):
    """客户端中途断开走 GeneratorExit：不能误报成服务端异常。"""
    inserted: list[dict] = []
    service, sess = _install_fakes(
        monkeypatch,
        events=[{"type": "text", "data": {"content": "x"}}],
        inserted=inserted,
        outcome=None,
    )

    async def half_dispatch(**kw):
        yield {"type": "text", "data": {"content": "第一句"}}
        yield {"type": "text", "data": {"content": "第二句"}}

    service.orchestrator.dispatch = lambda **kw: half_dispatch(**kw).__aiter__()

    gen = service.stream(user_id=sess.user_id, session_id=None, message="x")
    assert await gen.__anext__()  # 拿到 session 事件后即可视为已开始下发
    await gen.aclose()
    assert inserted == []


@pytest.mark.asyncio
async def test_reply_aggregates_and_persists_trace(monkeypatch):
    inserted: list[dict] = []
    service, sess = _install_fakes(
        monkeypatch,
        events=[
            {
                "type": "route",
                "data": {
                    "agent_name": "order_service",
                    "confidence": 0.9,
                    "reason": "r",
                    "intents": [],
                    "intent_total": 1,
                },
            },
            {"type": "text", "data": {"content": "已发货"}},
        ],
        inserted=inserted,
    )
    result = await service.reply(user_id=sess.user_id, session_id=None, message="x", trace_id="reply-trace")

    assert result["trace_id"] == "reply-trace"
    assert result["content"] == "已发货"
    assert result["routed_to"] == "order_service"
    assert inserted[0]["trace_id"] == "reply-trace"
    assert inserted[0]["response_chars"] == len("已发货")
    # 追问续接：上一轮 Agent 被透传给路由
    assert sess.last_route_to == "order_service"


@pytest.mark.asyncio
async def test_collector_disabled_skips_write(monkeypatch):
    monkeypatch.setattr(settings, "trace_enabled", False)
    inserted: list[dict] = []
    service, sess = _install_fakes(monkeypatch, events=[{"type": "text", "data": {"content": "x"}}], inserted=inserted)
    chunks = await _drain(service.stream(user_id=sess.user_id, session_id=None, message="x", trace_id="tid-off"))
    assert inserted == []
    # 开关关闭仍要回带 trace_id，日志关联不能断
    assert _payload(chunks[-1], "done")["trace_id"] == "tid-off"


@pytest.mark.asyncio
async def test_stream_sets_title_from_first_message(monkeypatch):
    """新建会话默认标题为占位值时，用首条用户消息自动命名（折叠空白+截断）。"""
    inserted: list[dict] = []
    service, sess = _install_fakes(
        monkeypatch,
        events=[{"type": "text", "data": {"content": "已发货"}}],
        inserted=inserted,
    )
    long_msg = "我想\n\n查询一下   这个订单到底什么时候能送到，急用！！！"
    await _drain(service.stream(user_id=sess.user_id, session_id=None, message=long_msg))

    assert sess.title.startswith("我想 查询一下")  # 换行/连续空白被折叠成单个空格
    assert len(sess.title) <= 25  # 24 字 + 省略号
    assert sess.title.endswith("…")


@pytest.mark.asyncio
async def test_renamed_session_title_not_overwritten(monkeypatch):
    """已被手动重命名的会话（不再是占位值）不能再被首条消息覆盖。"""
    inserted: list[dict] = []
    service, sess = _install_fakes(
        monkeypatch,
        events=[{"type": "text", "data": {"content": "已发货"}}],
        inserted=inserted,
    )
    sess.title = "我的售后问题"
    await _drain(service.stream(user_id=sess.user_id, session_id=None, message="另一个问题"))

    assert sess.title == "我的售后问题"
