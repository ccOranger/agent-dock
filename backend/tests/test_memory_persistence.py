"""记忆归档回归：memory_uid 稳定性 + working 起步生命周期 + 访问计数晋升（spec 8.1/8.6）。"""
import asyncio
import hashlib
import re
from types import SimpleNamespace

import pytest

from app.core import context_engineer as ce_mod
from app.core.config import settings
from app.core.context_engineer import ContextEngineer
from app.models import repositories as repo_mod


class _RecordingMemoryTool:
    """记录每次 tool.run 调用参数的假 MemoryTool"""

    def __init__(self):
        self.calls: list[dict] = []

    def run(self, parameters, *a, **kw):
        self.calls.append(parameters)
        return ""


def _row(
    memory_uid="s1:abc",
    type_="working",
    access_count=3,
    content="我的订单到哪了",
    source_message_id=None,
):
    return SimpleNamespace(
        user_id="22222222-2222-2222-2222-222222222222",
        memory_uid=memory_uid,
        type=type_,
        access_count=access_count,
        content=content,
        importance=0.42,
        session_id=None,
        source_message_id=source_message_id,
        metadata_={"agent_name": "order_service"},
    )


@pytest.fixture(autouse=True)
def _no_real_purge(monkeypatch):
    """兼容兜底：防止各 persist 测试触发的后台 sweep 真的去删开库里数据（单测内 purge 一律空操作）"""

    async def noop(ttl_seconds):
        return 0

    monkeypatch.setattr(repo_mod.MemoryRepo, "purge_expired_working", staticmethod(noop))


@pytest.mark.asyncio
async def test_persist_memory_uid_is_stable_sha1(monkeypatch):
    """persist_after_run 写入镜像时的 memory_uid 必须基于稳定摘要。

    此前用 abs(hash(query))，跨进程重启后 hash 值变化，会导致同一条记忆无法 upsert、
    重复入库。此处断言 uid 等于 sha1(query) 的前 16 位十六进制。
    """
    captured: dict = {}
    tool = _RecordingMemoryTool()

    async def fake_upsert_mirror(user_id, memory_uid, type_, content, **kw):
        captured["memory_uid"] = memory_uid
        captured["content"] = content

    monkeypatch.setattr(settings, "memory_importance_threshold", 0.0)
    monkeypatch.setattr(ce_mod, "get_memory_tool", lambda user_id: tool)
    monkeypatch.setattr(repo_mod.MemoryRepo, "upsert_mirror", fake_upsert_mirror)

    session_id = "11111111-1111-1111-1111-111111111111"
    query = "我的订单 ORD123456 到哪了"
    await ContextEngineer().persist_after_run(
        user_id="22222222-2222-2222-2222-222222222222",
        session_id=session_id,
        query=query,
        agent_name="order_service",
        history=[],
    )

    expected_suffix = hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]
    assert captured["memory_uid"] == f"{session_id}:{expected_suffix}"
    assert re.fullmatch(r"[0-9a-f]{16}", captured["memory_uid"].split(":", 1)[1])


@pytest.mark.asyncio
async def test_persist_writes_working_every_turn(monkeypatch):
    """spec 8.1：每轮都写 working 层，未达重要性阈值也不再直接丢弃。"""
    tool = _RecordingMemoryTool()
    mirrored: dict = {}

    async def fake_upsert_mirror(user_id, memory_uid, type_, content, **kw):
        mirrored["type"] = type_

    monkeypatch.setattr(settings, "memory_importance_threshold", 0.95)
    monkeypatch.setattr(ce_mod, "get_memory_tool", lambda user_id: tool)
    monkeypatch.setattr(repo_mod.MemoryRepo, "upsert_mirror", fake_upsert_mirror)

    await ContextEngineer().persist_after_run(
        user_id="u1",
        session_id="11111111-1111-1111-1111-111111111111",
        query="你好",
        agent_name="general",
        history=[],
    )

    assert tool.calls[0]["memory_type"] == "working"
    # 未达阈且无关键实体：只写 working，不晋升 episodic
    assert [c["memory_type"] for c in tool.calls] == ["working"]
    assert mirrored["type"] == "working"


@pytest.mark.asyncio
async def test_persist_promotes_on_importance_and_entity(monkeypatch):
    """spec 8.6：重要性达阈或检出关键实体 → 晋升 episodic 写入 + 镜像标晋升类型。"""
    tool = _RecordingMemoryTool()
    mirrored: dict = {}

    async def fake_upsert_mirror(user_id, memory_uid, type_, content, **kw):
        mirrored["type"] = type_

    monkeypatch.setattr(settings, "memory_importance_threshold", 0.0)
    monkeypatch.setattr(ce_mod, "get_memory_tool", lambda user_id: tool)
    monkeypatch.setattr(repo_mod.MemoryRepo, "upsert_mirror", fake_upsert_mirror)

    await ContextEngineer().persist_after_run(
        user_id="u1",
        session_id="11111111-1111-1111-1111-111111111111",
        query="我的订单 ORD123456 到哪了",
        agent_name="order_service",
        history=[],
    )

    types_written = [c["memory_type"] for c in tool.calls]
    assert types_written == ["working", "episodic"]
    # 带关键实体的镜像行标 semantic（展示语义）
    assert mirrored["type"] == "semantic"


@pytest.mark.asyncio
async def test_persist_records_source_message_id(monkeypatch):
    """镜像行必须溯源到当轮用户消息（与 messages 表的同源文本建立关联）。"""
    mirrored: dict = {}

    async def fake_upsert_mirror(user_id, memory_uid, type_, content, **kw):
        mirrored["source_message_id"] = kw.get("source_message_id")

    monkeypatch.setattr(settings, "memory_importance_threshold", 0.95)
    monkeypatch.setattr(ce_mod, "get_memory_tool", lambda user_id: _RecordingMemoryTool())
    monkeypatch.setattr(repo_mod.MemoryRepo, "upsert_mirror", fake_upsert_mirror)

    message_id = "33333333-3333-3333-3333-333333333333"
    await ContextEngineer().persist_after_run(
        user_id="u1",
        session_id="11111111-1111-1111-1111-111111111111",
        query="你好",
        agent_name="general",
        history=[],
        message_id=message_id,
    )
    assert mirrored["source_message_id"] == message_id

    # 未传 message_id（如后台手工修正）不能报错，落库为 None
    mirrored.clear()
    await ContextEngineer().persist_after_run(
        user_id="u1",
        session_id="11111111-1111-1111-1111-111111111111",
        query="你好",
        agent_name="general",
        history=[],
    )
    assert mirrored["source_message_id"] is None


def test_hit_prefixes_parses_memory_tool_format():
    """召回结果行需解析出内容前缀；截断产生的 ... 后缀要剥掉，头部行不参与计数。"""
    long_content = "订" * 100
    preview = long_content[:80] + "..."
    result = (
        "🔍 找到 2 条相关记忆:\n"
        "1. [情景记忆] 我要投诉，快递太慢了 (重要性: 0.38)\n"
        f"2. [工作记忆] {preview} (重要性: 0.55)"
    )
    prefixes = ContextEngineer._hit_prefixes(result)
    assert prefixes == ["我要投诉，快递太慢了", long_content[:80]]


@pytest.mark.asyncio
async def test_count_and_promote_on_access_threshold(monkeypatch):
    """spec 8.6：working 行 access_count 达阈 → 补写长期层并翻转镜像行类型。"""
    tool = _RecordingMemoryTool()
    upserts: list[dict] = []
    MID = "33333333-3333-3333-3333-333333333333"

    async def fake_touch_access(user_id, prefixes):
        assert prefixes == ["我要投诉"]
        # 一条达阈 working（带溯源消息）+ 一条已晋升的 episodic（不应再次晋升）
        return [
            _row(type_="working", access_count=3, source_message_id=MID),
            _row(memory_uid="s2:x", type_="episodic", access_count=9),
        ]

    async def fake_upsert_mirror(user_id, memory_uid, type_, content, **kw):
        upserts.append({"memory_uid": memory_uid, "type": type_, "mid": kw.get("source_message_id")})

    monkeypatch.setattr(settings, "memory_access_threshold", 3)
    monkeypatch.setattr(ce_mod, "get_memory_tool", lambda user_id: tool)
    monkeypatch.setattr(repo_mod.MemoryRepo, "touch_access", fake_touch_access)
    monkeypatch.setattr(repo_mod.MemoryRepo, "upsert_mirror", fake_upsert_mirror)

    await ContextEngineer()._count_and_promote("u1", ["我要投诉"])

    # 晋升重写时原溯源消息 id 必须随行携带，不能被抹成 NULL
    assert upserts == [{"memory_uid": "s1:abc", "type": "episodic", "mid": MID}]
    assert len(tool.calls) == 1
    assert tool.calls[0]["action"] == "add" and tool.calls[0]["memory_type"] == "episodic"


@pytest.mark.asyncio
async def test_promote_on_session_close(monkeypatch):
    """spec 8.6：会话关闭时该会话全部 working 记忆晋升为 episodic。"""
    tool = _RecordingMemoryTool()
    upserts: list[dict] = []

    async def fake_promote_by_session(user_id, session_id):
        return [_row(memory_uid="a1"), _row(memory_uid="a2", content="订单号44455666")]

    async def fake_upsert_mirror(user_id, memory_uid, type_, content, **kw):
        upserts.append({"memory_uid": memory_uid, "type": type_})

    monkeypatch.setattr(ce_mod, "get_memory_tool", lambda user_id: tool)
    monkeypatch.setattr(repo_mod.MemoryRepo, "promote_working_by_session", fake_promote_by_session)
    monkeypatch.setattr(repo_mod.MemoryRepo, "upsert_mirror", fake_upsert_mirror)

    await ContextEngineer().promote_on_session_close("u1", "s1")

    assert {u["memory_uid"] for u in upserts} == {"a1", "a2"}
    assert all(u["type"] == "episodic" for u in upserts)
    assert len(tool.calls) == 2


@pytest.mark.asyncio
async def test_persist_purges_expired_working(monkeypatch):
    """spec 8.1：persist 后触发过期 working 镜像行清理，且进程内节流只扫一次。"""
    purge_calls: list[int] = []

    async def fake_purge(ttl_seconds):
        purge_calls.append(ttl_seconds)
        return 1

    async def fake_upsert_mirror(user_id, memory_uid, type_, content, **kw):
        return None

    monkeypatch.setattr(settings, "memory_importance_threshold", 0.95)
    monkeypatch.setattr(ce_mod, "get_memory_tool", lambda user_id: _RecordingMemoryTool())
    monkeypatch.setattr(repo_mod.MemoryRepo, "upsert_mirror", fake_upsert_mirror)
    monkeypatch.setattr(repo_mod.MemoryRepo, "purge_expired_working", fake_purge)
    monkeypatch.setattr(ce_mod, "_last_working_purge", None)  # None = 本进程从未清理过

    engineer = ContextEngineer()
    await engineer.persist_after_run(
        user_id="u1",
        session_id="11111111-1111-1111-1111-111111111111",
        query="你好",
        agent_name="general",
        history=[],
    )
    for _ in range(10):  # 让后台 sweep 任务得到执行机会
        await asyncio.sleep(0)
    assert purge_calls == [settings.working_memory_ttl]

    # 节流窗口内第二轮 → 不再重复清理
    await engineer.persist_after_run(
        user_id="u1",
        session_id="11111111-1111-1111-1111-111111111111",
        query="在吗",
        agent_name="general",
        history=[],
    )
    for _ in range(10):
        await asyncio.sleep(0)
    assert len(purge_calls) == 1
