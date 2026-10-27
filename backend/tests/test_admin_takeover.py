"""人工接管：状态机仓储、escalate 联动、让位契约与坐席工作台接口（Repo 全 mock，不触真实 DB）"""
import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents import orchestrator as orch_mod
from app.agents.orchestrator import Orchestrator
from app.api.v1 import admin_takeover as at
from app.core.constants import SessionStatus
from app.models import repositories as repo_mod
from app.models.entities import StaffUser
from app.models.repositories import AuditRepo, MessageRepo, SessionRepo, TicketRepo
from app.schemas.scene import DEFAULT_SCENE_ID, SceneContext
from app.services import chat_service as cs_mod
from app.services.chat_service import ChatService
from app.tools.custom.ticket_tool import EscalateTool

SESSION_UUID = "11111111-1111-1111-1111-111111111111"
OTHER_UUID = "66666666-6666-6666-6666-666666666666"
STAFF_UUID = "55555555-5555-5555-5555-555555555555"

_DEFAULT_SCENE = SceneContext(
    scene_id=DEFAULT_SCENE_ID,
    code="default",
    is_default=True,
    router_mode="direct",
    agent_names=("default_assistant",),
    direct_agent="default_assistant",
)


# ==========================================================================
# 仓储层：mark_handover / claim / release（状态机守卫）
# ==========================================================================
class FakeDB:
    """按模型分派的最小 session：Session→会话行，StaffUser→坐席行。"""

    def __init__(self, sess_row, staff_row=None):
        self.sess_row = sess_row
        self.staff_row = staff_row
        self.commits = 0

    async def get(self, model, pk):
        if model is StaffUser:
            return self.staff_row
        return self.sess_row

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_db(monkeypatch, sess_row, staff_row=None):
    monkeypatch.setattr(repo_mod, "SessionLocal", lambda: FakeDB(sess_row, staff_row))


def _session_row(status="handover", assigned_agent=None):
    # assigned_agent 存的是 UUID 对象（ORM 行为），claim/release 按 UUID 比较
    return SimpleNamespace(
        id=SESSION_UUID,
        status=status,
        assigned_agent=_as_uuid(assigned_agent),
        updated_at=None,
        scene_id=DEFAULT_SCENE_ID,
    )


def _as_uuid(value):
    return uuid.UUID(str(value)) if value else None


async def test_mark_handover_only_from_active(monkeypatch):
    row = _session_row(status="active")
    _patch_db(monkeypatch, row)
    assert await SessionRepo.mark_handover(SESSION_UUID) is True
    assert row.status == "handover"

    # 已接入 / 已结束的会话不能被排队写覆盖
    _patch_db(monkeypatch, _session_row(status="human_handling"))
    assert await SessionRepo.mark_handover(SESSION_UUID) is False

    # 非 UUID（示例客户名等）静默跳过，绝不抛错阻断工具链路
    assert await SessionRepo.mark_handover("not-a-uuid") is False
    assert await SessionRepo.mark_handover(None) is False


async def test_claim_takeover_binds_staff_and_status(monkeypatch):
    row = _session_row(status="handover")
    _patch_db(monkeypatch, row)
    result = await SessionRepo.claim_takeover(SESSION_UUID, STAFF_UUID)
    assert result is row
    assert row.status == SessionStatus.HUMAN_HANDLING
    assert str(row.assigned_agent) == STAFF_UUID


async def test_claim_takeover_owner_guards(monkeypatch):
    # 他人已接入 → True（409 语义）
    _patch_db(monkeypatch, _session_row(status="human_handling", assigned_agent=OTHER_UUID))
    assert await SessionRepo.claim_takeover(SESSION_UUID, STAFF_UUID) is True

    # 同一坐席重复 claim 幂等成功（断线回到自己的会话）
    row = _session_row(status="human_handling", assigned_agent=STAFF_UUID)
    _patch_db(monkeypatch, row)
    assert await SessionRepo.claim_takeover(SESSION_UUID, STAFF_UUID) is row

    # active 会话不在队列里 → 不能直接接入
    _patch_db(monkeypatch, _session_row(status="active"))
    assert await SessionRepo.claim_takeover(SESSION_UUID, STAFF_UUID) is True


async def test_release_takeover_state_and_owner_guard(monkeypatch):
    row = _session_row(status="human_handling", assigned_agent=STAFF_UUID)
    _patch_db(monkeypatch, row, staff_row=SimpleNamespace(id=_as_uuid(STAFF_UUID), role="agent"))
    assert await SessionRepo.release_takeover(SESSION_UUID, STAFF_UUID, to_status="active") is True
    assert row.status == "active"
    # assigned_agent 保留，作为「谁处理过」的追溯
    assert str(row.assigned_agent) == STAFF_UUID

    # 非接手坐席（且非 admin）→ False
    _patch_db(
        monkeypatch,
        _session_row(status="human_handling", assigned_agent=STAFF_UUID),
        staff_row=SimpleNamespace(id=_as_uuid(OTHER_UUID), role="agent"),
    )
    assert await SessionRepo.release_takeover(SESSION_UUID, OTHER_UUID) is False

    # admin 可调岗
    _patch_db(
        monkeypatch,
        _session_row(status="human_handling", assigned_agent=STAFF_UUID),
        staff_row=SimpleNamespace(id=_as_uuid(OTHER_UUID), role="admin"),
    )
    assert await SessionRepo.release_takeover(SESSION_UUID, OTHER_UUID, to_status="closed") is True

    # 不在接管中（仍排队 / 已释放）→ False
    _patch_db(monkeypatch, _session_row(status="handover"))
    assert await SessionRepo.release_takeover(SESSION_UUID, STAFF_UUID) is False


# ==========================================================================
# chat_service：handover 事件置状态 + 接管让位（不跑编排、不产生机器人应答）
# ==========================================================================
class FakeSession:
    def __init__(self, status="active"):
        self.id = SESSION_UUID
        self.user_id = "22222222-2222-2222-2222-222222222222"
        self.visitor_id = None
        self.scene_id = DEFAULT_SCENE_ID
        self.last_route_to = "order_service"
        self.title = "投诉处理"
        self.status = status


def _install_service_fakes(monkeypatch, events, *, status="active", outcome=None):
    """假仓储：记录消息角色/编排调用次数/触达的会话，让位契约靠这几个断言。"""
    sess = FakeSession(status=status)
    calls: dict = {"roles": [], "dispatched": 0, "touched": [], "rows": []}

    class FakeDBCtx:
        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_add(session_id, role, content, routed_to=None, db=None, **kw):
        calls["roles"].append(role)
        return SimpleNamespace(id="3" * 32 if role == "user" else "4" * 32, content=content)

    async def fake_dispatch(self, **kw):
        calls["dispatched"] += 1
        collector = kw["collector"]
        if outcome:
            collector.record_outcome(outcome)
        for event in events:
            yield event

    async def fake_touch(session_id, db=None):
        calls["touched"].append(str(session_id))

    async def fake_insert(row):
        calls["rows"].append(row)

    async def noop(**kw):
        return None

    async def fake_get_or_create(actor, scene_id, session_id, db=None):
        return sess

    async def fake_list_recent(session_id, limit=50, db=None):
        return [{"role": "user", "content": "上一轮问题"}]

    async def fake_resolve_scene(scene_code=None):
        return _DEFAULT_SCENE

    monkeypatch.setattr(cs_mod, "SessionLocal", FakeDBCtx)
    monkeypatch.setattr(cs_mod, "resolve_scene", fake_resolve_scene)
    monkeypatch.setattr(cs_mod.SessionRepo, "get_or_create_for_actor", fake_get_or_create)
    monkeypatch.setattr(cs_mod.SessionRepo, "touch", fake_touch)
    monkeypatch.setattr(cs_mod.MessageRepo, "add", fake_add)
    monkeypatch.setattr(cs_mod.MessageRepo, "list_recent", fake_list_recent)
    monkeypatch.setattr(cs_mod.TraceRepo, "insert", fake_insert)
    monkeypatch.setattr(cs_mod.ChatEventRepo, "attach_identity", noop)
    monkeypatch.setattr(cs_mod.ChatEventRepo, "record_usage", noop)
    monkeypatch.setattr(cs_mod.VisitorRepo, "get_or_create", noop)

    service = ChatService()
    service.orchestrator.dispatch = lambda **kw: fake_dispatch(None, **kw).__aiter__()
    return service, sess, calls


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


async def test_stream_marks_session_handover_on_event(monkeypatch):
    """低置信 / escalate 补发的 handover 事件必须把会话置为 handover，并不再落助手消息。"""
    service, sess, calls = _install_service_fakes(
        monkeypatch,
        events=[
            {
                "type": "route",
                "data": {
                    "agent_name": "complaint_handler",
                    "confidence": 0.9,
                    "reason": "r",
                    "intents": [],
                    "intent_total": 1,
                },
            },
            {"type": "text", "data": {"content": "已为您转人工"}},
            {"type": "handover", "data": {"reason": "escalated_by_tool"}},
        ],
        outcome="answered",
    )
    chunks = await _drain(
        service.stream(user_id=sess.user_id, session_id=None, message="帮我升级处理这个问题")
    )

    assert sess.status == SessionStatus.HANDOVER
    # break 后生成的文本不再落库：客户端自己渲染转人工提示，避免堆积空助手消息
    assert calls["roles"] == ["user"]
    assert _payload(chunks[-1], "done")["takeover"] is None
    # 结局改写为 handover：转人工率统计与「转人工全量落库」都依赖它
    assert calls["rows"][0]["outcome"] == "handover"


async def test_stream_keyword_explicit_handover(monkeypatch):
    """客户明说“转人工”：确定性置 handover、不走编排（default_assistant 无升级工具也能进队列）。"""
    service, sess, calls = _install_service_fakes(
        monkeypatch,
        events=[{"type": "text", "data": {"content": "不该出现的机器人应答"}}],
        outcome="answered",
    )
    chunks = await _drain(service.stream(user_id=sess.user_id, session_id=None, message="转人工"))

    # 关键词分支在编排之前短路：完全不调 dispatch
    assert calls["dispatched"] == 0
    assert sess.status == SessionStatus.HANDOVER
    assert calls["roles"] == ["user"]
    done = _payload(chunks[-1], "done")
    assert done["takeover"] is True
    assert done["session_status"] == SessionStatus.HANDOVER
    assert calls["rows"][0]["outcome"] == "handover"


async def test_takeover_session_skips_orchestration(monkeypatch):
    """坐席已接入：客户消息只落库，不跑编排、不产生机器人应答。"""
    service, sess, calls = _install_service_fakes(
        monkeypatch, events=[{"type": "text", "data": {"content": "不该出现"}}], status="human_handling"
    )
    chunks = await _drain(service.stream(user_id=sess.user_id, session_id=None, message="补充一个问题"))

    assert calls["dispatched"] == 0
    assert calls["roles"] == ["user"]
    assert calls["touched"] == [sess.id]
    assert sess.status == SessionStatus.HUMAN_HANDLING
    done = _payload(chunks[-1], "done")
    assert done["takeover"] is True
    assert done["session_status"] == SessionStatus.HUMAN_HANDLING
    assert "不该出现" not in "".join(chunks)


async def test_queued_handover_also_yields_to_bot(monkeypatch):
    """已排队但坐席尚未接入：同样让位，客户追问不再拿到机器人应答。"""
    service, sess, calls = _install_service_fakes(monkeypatch, events=[], status="handover")
    chunks = await _drain(service.stream(user_id=sess.user_id, session_id=None, message="还在吗"))
    assert calls["dispatched"] == 0
    assert _payload(chunks[1], "status")["stage"] == "takeover"
    assert _payload(chunks[1], "status")["session_status"] == SessionStatus.HANDOVER


async def test_reply_takeover_returns_without_answer(monkeypatch):
    service, sess, calls = _install_service_fakes(monkeypatch, events=[], status="human_handling")
    result = await service.reply(user_id=sess.user_id, session_id=None, message="x")
    assert result["takeover"] is True
    assert result["content"] == ""
    assert result["session_status"] == SessionStatus.HUMAN_HANDLING
    assert calls["dispatched"] == 0


# ==========================================================================
# escalate 工具 → 会话状态；orchestrator → 补发 handover 事件
# ==========================================================================
async def test_escalate_tool_marks_session_handover(monkeypatch):
    marked: list[str] = []

    async def fake_latest_open_for(*, user_id, session_id):
        return None

    async def fake_write(**kw):
        return None

    async def fake_mark_handover(session_id):
        marked.append(str(session_id))
        return True

    monkeypatch.setattr(TicketRepo, "latest_open_for", fake_latest_open_for)
    monkeypatch.setattr(AuditRepo, "write", fake_write)
    monkeypatch.setattr(SessionRepo, "mark_handover", fake_mark_handover)

    result = await EscalateTool().run(session_id=SESSION_UUID, reason="客户情绪激动")
    assert result["data"]["handover"] is True
    assert marked == [SESSION_UUID]


async def test_escalate_tool_survives_status_write_failure(monkeypatch):
    """状态写是旁路：失败也不能让升级工具报错、中断对话。"""

    async def fake_latest_open_for(*, user_id, session_id):
        return None

    async def fake_write(**kw):
        return None

    async def boom(session_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(TicketRepo, "latest_open_for", fake_latest_open_for)
    monkeypatch.setattr(AuditRepo, "write", fake_write)
    monkeypatch.setattr(SessionRepo, "mark_handover", boom)

    result = await EscalateTool().run(session_id=SESSION_UUID, reason="资金安全")
    assert result["ok"] is True


class _FakeRouter:
    async def route(self, query, **kw):
        return {
            "intents": [{"agent_name": "complaint_handler", "confidence": 0.9, "reason": "r"}],
            "agent_name": "complaint_handler",
            "confidence": 0.9,
            "reason": "r",
            "fallback_agent": None,
        }


class _FakeCtx:
    async def build(self, user_id, query, rag_namespaces, collector=None, runtime=None):
        return query

    async def persist_after_run(self, **kw):
        return None


def _make_orch(monkeypatch, tool_events):
    orch = Orchestrator()
    orch.router = _FakeRouter()
    orch.ctx_engineer = _FakeCtx()

    async def fake_resolve(name, user_id, session_id=None):
        return SimpleNamespace(name=name)

    monkeypatch.setattr(orch_mod, "resolve_business_agent", fake_resolve)
    monkeypatch.setattr(orch_mod, "CONFIDENCE_THRESHOLD", 0.5)

    async def fake_run(business_agent, user_id, query, **kwargs):
        for event in tool_events:
            yield event
        yield {"type": "text", "data": {"content": "已为您转人工"}}

    orch._run_business_agent = fake_run
    return orch


async def _orch_events(orch):
    return [
        e
        async for e in orch.dispatch(user_id="u1", session_id="s1", query="转人工", history=[])
        if e["type"] != "status"
    ]


async def test_orchestrator_emits_handover_after_escalate_tool(monkeypatch):
    escalate_result = (
        "🔧 工具 escalate 执行结果：\n"
        "{'ok': True, 'data': {'session_id': 's1', 'handover': True, 'ticket_id': None}}"
    )
    orch = _make_orch(
        monkeypatch,
        [
            {"type": "tool_call", "data": {"id": "c-0", "name": "escalate", "args": {"reason": "x"}}},
            {
                "type": "tool_result",
                "data": {"id": "c-0", "name": "escalate", "result": escalate_result, "error": None},
            },
        ],
    )
    events = await _orch_events(orch)

    assert events[-1]["type"] == "handover"
    assert events[-1]["data"]["reason"] == "escalated_by_tool"
    # Agent 的转人工说明文本仍照常下发
    assert "已为您转人工" in "".join(e["data"]["content"] for e in events if e["type"] == "text")


async def test_orchestrator_no_handover_when_escalate_fails(monkeypatch):
    orch = _make_orch(
        monkeypatch,
        [
            {
                "type": "tool_result",
                "data": {
                    "id": "c-0",
                    "name": "escalate",
                    "result": "❌ 工具 escalate 执行异常：db down",
                    "error": None,
                },
            }
        ],
    )
    events = await _orch_events(orch)
    assert "handover" not in [e["type"] for e in events]


async def test_orchestrator_handover_for_it_escalation(monkeypatch):
    orch = _make_orch(
        monkeypatch,
        [
            {
                "type": "tool_result",
                "data": {
                    "id": "c-0",
                    "name": "escalate_it",
                    "result": "🔧 工具 escalate_it 执行结果：\n{'data': {'handover': True}}",
                    "error": None,
                },
            }
        ],
    )
    events = await _orch_events(orch)
    assert events[-1]["type"] == "handover"


# ==========================================================================
# MessageRepo：坐席消息落库 / 机器人上下文排除 / 增量游标
# ==========================================================================
class RecordingSession:
    def __init__(self, result_rows=None, scalar_rows=None):
        self.executed: list = []
        self.result_rows = result_rows or []
        self.scalar_rows = scalar_rows or []
        self.added: list = []

    async def execute(self, stmt):
        self.executed.append(stmt)

        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def scalars(self):
                return self

            def all(self):
                return self._rows

        return _Result(self.result_rows)

    async def get(self, model, pk):
        return None

    def add(self, obj):
        # 同步方法：与真实 AsyncSession.add 一致
        self.added.append(obj)

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_message_add_persists_metadata(monkeypatch):
    db = RecordingSession()
    monkeypatch.setattr(repo_mod, "SessionLocal", lambda: db)
    msg = await MessageRepo.add(
        SESSION_UUID, role="assistant", content="您好", routed_to="staff", metadata={"sender_type": "staff"}
    )
    assert db.added and db.added[0].metadata_ == {"sender_type": "staff"}
    assert msg is db.added[0]


async def test_list_recent_excludes_staff_messages(monkeypatch):
    db = RecordingSession(result_rows=[])
    monkeypatch.setattr(repo_mod, "SessionLocal", lambda: db)
    await MessageRepo.list_recent(SESSION_UUID, limit=10)
    compiled = db.executed[0].compile()
    sql = str(compiled)
    # ->> 右操作数会被绑定为参数，断言分两块：SQL 结构 + 绑定值
    assert "->>" in sql and "IS NULL" in sql
    bound = {str(v) for v in compiled.params.values()}
    assert "sender_type" in bound and "staff" in bound


# ==========================================================================
# 坐席工作台接口
# ==========================================================================
@pytest.fixture
def client(make_test_app):
    app = make_test_app([at.router], prefix="")
    return TestClient(make_test_app.as_role(app, "agent"))


def test_queue_passes_filters_and_alias(client, monkeypatch):
    captured: dict = {}

    async def fake_queue(*, scene_id=None, status=None, page=1, page_size=20):
        captured.update(scene_id=scene_id, status=status, page=page, page_size=page_size)
        return [{"id": SESSION_UUID, "status": "handover"}], 1

    async def fake_scene_by_code(code):
        # 只认已知场景；ghost 返回 None 验证 400 分支
        if code == "ghost":
            return None
        return SimpleNamespace(id=DEFAULT_SCENE_ID, code=code)

    monkeypatch.setattr(at.SessionRepo, "takeover_queue", fake_queue)
    monkeypatch.setattr(at.SceneRepo, "get_by_code", fake_scene_by_code)

    resp = client.get("/admin/takeover/sessions?status=queued&scene_code=cs_default&page=2")
    assert resp.status_code == 200
    assert captured["status"] == SessionStatus.HANDOVER
    assert captured["scene_id"] == DEFAULT_SCENE_ID
    assert resp.json()["data"]["total"] == 1

    # 已接入档别名映射到 human_handling，缺省不过滤时也要传 None
    assert client.get("/admin/takeover/sessions?status=handling").status_code == 200
    assert captured["status"] == SessionStatus.HUMAN_HANDLING

    assert client.get("/admin/takeover/sessions?status=nope").status_code == 400
    assert client.get("/admin/takeover/sessions?scene_code=ghost").status_code == 400


def test_queue_rejects_customer_role(make_test_app):
    app = make_test_app([at.router], prefix="")
    customer = TestClient(make_test_app.as_role(app, "customer"))
    assert customer.get("/admin/takeover/sessions").status_code == 403


def _dashed(digits: str) -> str:
    """parse_uuid 会把 32 位 hex 归一化成 36 位带连字符形式，测试按同样口径比对。"""
    return str(uuid.UUID(digits))


def test_claim_maps_conflict_and_missing(client, monkeypatch):
    missing_id = _dashed("0" * 32)
    taken_id = _dashed("2" * 32)

    async def fake_claim(session_id, staff_id):
        if session_id == missing_id:
            return None
        if session_id == taken_id:
            return True
        return SimpleNamespace(id=session_id, status="human_handling", assigned_agent=STAFF_UUID)

    monkeypatch.setattr(at.SessionRepo, "claim_takeover", fake_claim)

    ok = client.post(f"/admin/takeover/sessions/{SESSION_UUID}/claim")
    assert ok.status_code == 200
    assert ok.json()["data"]["status"] == "human_handling"
    assert ok.json()["data"]["assigned_agent_id"] == STAFF_UUID

    taken = client.post(f"/admin/takeover/sessions/{taken_id}/claim")
    assert taken.status_code == 409
    assert taken.json()["detail"]["code"] == 3002

    missing = client.post(f"/admin/takeover/sessions/{missing_id}/claim")
    assert missing.status_code == 404

    # 非 UUID 路径参数 → 400
    assert client.post("/admin/takeover/sessions/not-a-uuid/claim").status_code == 400


def test_send_message_state_owner_and_content_guards(make_test_app, monkeypatch):
    added: list[dict] = []

    async def fake_add(session_id, role, content, routed_to=None, db=None, metadata=None, **kw):
        added.append({"role": role, "content": content, "routed_to": routed_to, "metadata": metadata})
        return SimpleNamespace(id="9" * 32, created_at=datetime(2026, 10, 27))

    def _sess(status, assigned):
        async def fake_get(session_id):
            return SimpleNamespace(id=SESSION_UUID, status=status, assigned_agent=assigned, scene_id=None)

        return fake_get

    monkeypatch.setattr(at.MessageRepo, "add", fake_add)

    app = make_test_app([at.router], prefix="")
    staff_client = TestClient(make_test_app.as_role(app, "agent"))  # stub 坐席 id = "u-1"

    # 他人接入 → 403
    monkeypatch.setattr(at.SessionRepo, "get", _sess("human_handling", "other-staff"))
    assert (
        staff_client.post(f"/admin/takeover/sessions/{SESSION_UUID}/messages", json={"content": "您好"}).status_code
        == 403
    )

    # 仍在排队（未 claim）→ 409
    monkeypatch.setattr(at.SessionRepo, "get", _sess("handover", None))
    assert (
        staff_client.post(f"/admin/takeover/sessions/{SESSION_UUID}/messages", json={"content": "您好"}).status_code
        == 409
    )

    # 本人接入 → 成功，坐席标识写入 metadata/routed_to，内容两端去空白
    monkeypatch.setattr(at.SessionRepo, "get", _sess("human_handling", "u-1"))
    resp = staff_client.post(
        f"/admin/takeover/sessions/{SESSION_UUID}/messages", json={"content": "  您好，人工客服  "}
    )
    assert resp.status_code == 200
    assert added[0]["role"] == "assistant"
    assert added[0]["routed_to"] == "staff"
    assert added[0]["content"] == "您好，人工客服"
    assert added[0]["metadata"]["sender_type"] == "staff"

    # 会话不存在 → 404
    async def fake_get_none(session_id):
        return None

    monkeypatch.setattr(at.SessionRepo, "get", fake_get_none)
    assert (
        staff_client.post(f"/admin/takeover/sessions/{SESSION_UUID}/messages", json={"content": "您好"}).status_code
        == 404
    )


def test_release_maps_results_and_default_target(client, monkeypatch):
    released: dict = {}
    missing_id = _dashed("0" * 32)
    taken_id = _dashed("2" * 32)

    async def fake_release(session_id, staff_id, *, to_status="active"):
        released["to_status"] = to_status
        if session_id == missing_id:
            return None
        if session_id == taken_id:
            return False
        return True

    monkeypatch.setattr(at.SessionRepo, "release_takeover", fake_release)

    ok = client.post(f"/admin/takeover/sessions/{SESSION_UUID}/release", json={"to": "bot"})
    assert ok.status_code == 200
    assert released["to_status"] == "active"

    closed = client.post(f"/admin/takeover/sessions/{SESSION_UUID}/release", json={"to": "close"})
    assert released["to_status"] == "closed"
    assert closed.json()["data"]["status"] == "closed"

    # 缺省交还机器人
    assert client.post(f"/admin/takeover/sessions/{SESSION_UUID}/release").status_code == 200
    assert client.post(f"/admin/takeover/sessions/{taken_id}/release").status_code == 409
    assert client.post(f"/admin/takeover/sessions/{missing_id}/release").status_code == 404


def test_detail_returns_session_and_incremental_messages(client, monkeypatch):
    async def fake_get(session_id):
        return SimpleNamespace(
            id=SESSION_UUID,
            status="human_handling",
            assigned_agent=STAFF_UUID,
            scene_id=None,
            title="投诉",
            user_id=None,
            visitor_id=None,
            last_route_to="complaint_handler",
            created_at=datetime(2026, 10, 27, 10, 0, 0),
            updated_at=datetime(2026, 10, 27, 10, 5, 0),
        )

    async def fake_staff(staff_id):
        return SimpleNamespace(username="agent01", display_name="客服小李")

    seen: dict = {}

    async def fake_list_after(session_id, after_id=None):
        seen["after_id"] = after_id
        return [{"id": "9" * 32, "role": "assistant", "sender_type": "staff", "staff_name": None}]

    async def fake_list(session_id, page=None, page_size=None):
        return [{"id": "8" * 32, "role": "user", "sender_type": None}]

    monkeypatch.setattr(at.SessionRepo, "get", fake_get)
    monkeypatch.setattr(at.StaffRepo, "get_by_id", fake_staff)
    monkeypatch.setattr(at.MessageRepo, "list_by_session_after", fake_list_after)
    monkeypatch.setattr(at.MessageRepo, "list_by_session", fake_list)

    full = client.get(f"/admin/takeover/sessions/{SESSION_UUID}")
    assert full.status_code == 200
    data = full.json()["data"]
    assert data["session"]["assigned_agent"]["name"] == "客服小李"
    assert data["messages"][0]["id"] == "8" * 32

    delta = client.get(f"/admin/takeover/sessions/{SESSION_UUID}?after_id={'8' * 32}")
    assert seen["after_id"] == "8" * 32
    assert delta.json()["data"]["messages"][0]["sender_type"] == "staff"
