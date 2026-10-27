"""链路采集器单测：span 结构、载荷裁剪、采样与拍平维度（纯内存，不碰 DB）"""
import json

from app.core.config import settings
from app.core.tracing import (
    OUTCOME_ANSWERED,
    OUTCOME_ERROR,
    OUTCOME_HANDOVER,
    SPAN_RUN,
    SPAN_TOOL,
    TraceCollector,
    clip_payload,
    new_request_trace_id,
)


def _collector(**kw):
    return TraceCollector(trace_id="t-1", user_id="u-1", session_id="s-1", **kw)


def _span(collector, type_):
    matched = [s for s in collector.spans if s["type"] == type_]
    assert matched, f"missing span {type_}"
    return matched[0]


# ---------------------------------------------------------------- 基础
def test_new_request_trace_id_is_16_hex_grepable():
    tid = new_request_trace_id()
    assert len(tid) == 16
    int(tid, 16)  # 可 hex 解析，能与 structlog / X-Trace-Id 直接对比
    assert tid != new_request_trace_id()


def test_chat_endpoint_reuses_middleware_trace_id():
    """响应头、日志与 request_traces 必须是同一个 trace_id。

    端点自己再 new 一个会造成“拿着 X-Trace-Id 查不到链路”（运维与
    评测执行器的 RAG/token 回读都靠这个 id 对齐）。
    """
    from types import SimpleNamespace

    from app.api.v1.chat import _request_trace_id

    request = SimpleNamespace(state=SimpleNamespace(trace_id="ab" * 8))
    assert _request_trace_id(request) == "ab" * 8
    # 没有中间件（如精简测试应用）时自行生成，仍是可 grep 的 16 位 hex
    minted = _request_trace_id(SimpleNamespace(state=SimpleNamespace()))
    assert len(minted) == 16
    int(minted, 16)


def test_disabled_collector_records_nothing():
    c = _collector(enabled=False)
    c.add(SPAN_RUN, "knowledge_qa")
    key = c.start(SPAN_TOOL, "tool")
    c.end(key, status="ok")
    c.record_tool_call(name="order_query", args={"id": 1})
    c.add_unique("tool_names", "order_query")
    c.record_intents([{"agent_name": "knowledge_qa", "confidence": 0.9, "reason": "r"}], 2)
    c.record_outcome(OUTCOME_ANSWERED)
    c.mark_first_token()
    c.mark_finished()
    assert c.spans == []
    assert c.should_persist() is False
    assert c.tool_call_count == 0
    assert c.ttft_ms is None and c.total_latency_ms is None


def test_trace_enabled_global_switch_disables_collection(monkeypatch):
    monkeypatch.setattr(settings, "trace_enabled", False)
    c = _collector()
    assert c.enabled is False
    assert c.add(SPAN_RUN, "x") == ""
    # start/end 返回空 key 时，后续 end 必须安静接受
    c.end(c.start(SPAN_TOOL, "x"))
    assert c.spans == []


# ---------------------------------------------------------------- span
def test_add_and_start_end_span_shape():
    c = _collector()
    sid = c.add(SPAN_RUN, "order_service", intent_index=0, extra=None, model="deepseek")
    span = _span(c, SPAN_RUN)
    assert span["span_id"] == sid
    assert span["parent_id"] is None
    assert span["status"] == "ok"
    assert span["duration_ms"] >= 0
    # None 属性不入表，避免 spans JSONB 里塞满噪声
    assert span["attributes"] == {"intent_index": 0, "model": "deepseek"}

    key = c.start(SPAN_TOOL, "order_query", agent_name="order_service")
    # start() 只登记未完成 span，未 end 前不得混入时间线
    assert all(s["type"] != SPAN_TOOL for s in c.spans)
    c.end(key, result="ok-result", error=None)
    tool = _span(c, SPAN_TOOL)
    assert tool["span_id"] == key
    assert tool["attributes"]["agent_name"] == "order_service"
    assert tool["attributes"]["result"] == "ok-result"
    assert "error" not in tool["attributes"]
    assert len([s for s in c.spans if s["type"] == SPAN_TOOL]) == 1


def test_end_unknown_or_empty_key_is_noop():
    c = _collector()
    c.end("not-exists", status="ok")
    c.end("", status="ok")
    assert c.spans == []


def test_spans_are_json_serializable():
    """spans 要写进 JSONB，任何不可序列化对象都会在 insert 时才炸，这里提前兜住。"""
    c = _collector()
    c.record_tool_call(name="t", args={"嵌套": [1, {"x": None}]})
    c.record_intents([{"agent_name": "a", "confidence": 0.8, "reason": "理由"}], 2)
    c.record_outcome(OUTCOME_ANSWERED)
    json.dumps(c.to_row()["spans"], ensure_ascii=False)


# ---------------------------------------------------------------- 载荷裁剪
def test_clip_payload_truncates_with_flag(monkeypatch):
    monkeypatch.setattr(settings, "trace_store_payload", True)
    monkeypatch.setattr(settings, "trace_payload_max_chars", 10)
    text, truncated = clip_payload("0123456789ABCDEF")
    assert text == "0123456789"
    assert truncated is True
    assert clip_payload("short") == ("short", False)


def test_clip_payload_dict_uses_json(monkeypatch):
    monkeypatch.setattr(settings, "trace_store_payload", True)
    text, _ = clip_payload({"a": 1})
    assert json.loads(text) == {"a": 1}


def test_clip_payload_omitted_when_store_disabled(monkeypatch):
    """关闭开关后只留字节数，链路表不能变成客户数据仓库。"""
    monkeypatch.setattr(settings, "trace_store_payload", False)
    text, truncated = clip_payload("敏感手机号 13800000000")
    assert text.startswith("<payload omitted:")
    assert "敏感" not in text
    assert truncated is True
    assert clip_payload(None) == ("", False)


def test_tool_payload_respects_store_switch(monkeypatch):
    monkeypatch.setattr(settings, "trace_store_payload", False)
    c = _collector()
    key = c.record_tool_call(name="order_query", args={"phone": "13800000000"})
    c.finish_tool_call(key, result={"address": "某小区"})
    attrs = _span(c, SPAN_TOOL)["attributes"]
    assert "13800000000" not in json.dumps(attrs, ensure_ascii=False)
    assert attrs["args_truncated"] is True and attrs["result_truncated"] is True


# ---------------------------------------------------------------- 语义记录
def test_record_intents_flattens_count_and_confidence():
    c = _collector()
    intents = [
        {"agent_name": "order_service", "confidence": 0.91, "reason": "查订单"},
        {"agent_name": "complaint_handler", "confidence": 0.7, "reason": "投诉"},
        {"agent_name": "knowledge_qa", "confidence": 0.6, "reason": "咨询"},
    ]
    c.record_intents(intents, max_intents=2)
    assert c.intent_count == 3  # 总数按模型给的意图数，不受 max_intents 影响
    assert c.confidence == 0.91  # 主意图置信度用于后台按低置信筛查
    intent_spans = [s for s in c.spans if s["type"] == "route.intent"]
    assert [s["name"] for s in intent_spans] == ["order_service", "complaint_handler"]
    assert [s["attributes"]["intent_index"] for s in intent_spans] == [0, 1]


def test_record_intents_carries_evidence_fields():
    """意图依据（命中词 + 命中状况）要进 span，后台才能看出这句话为何判给该 Agent。"""
    c = _collector()
    c.record_intents(
        [
            {
                "agent_name": "order_service",
                "confidence": 0.9,
                "reason": "查订单",
                "matched_keywords": ["订单", "物流"],
                "clue_basis": "keyword_hit",
            },
            {
                "agent_name": "refund_service",
                "confidence": 0.8,
                "reason": "退款",
                "matched_keywords": [],
                "clue_basis": "no_keywords",
            },
        ],
        max_intents=2,
    )
    first, second = [s for s in c.spans if s["type"] == "route.intent"]
    assert first["attributes"]["matched_keywords"] == ["订单", "物流"]
    assert first["attributes"]["clue_basis"] == "keyword_hit"
    # 没命中就不写空数组，保持 span 精简；但状况字段仍要留下（前端要区分“没中”与“没配”）
    assert "matched_keywords" not in second["attributes"]
    assert second["attributes"]["clue_basis"] == "no_keywords"


def test_add_unique_dedupes_dimension_lists():
    c = _collector()
    for _ in range(3):
        c.add_unique("tool_names", "order_query")
    c.add_unique("tool_names", "")
    c.add_unique("tool_names", None)
    c.add_unique("agent_names", "order_service")
    assert c.tool_names == ["order_query"]
    assert c.agent_names == ["order_service"]


def test_tool_call_counting_and_error():
    c = _collector()
    ok_key = c.record_tool_call(name="order_query", args={"no": "A1"}, agent_name="order_service")
    c.finish_tool_call(ok_key, result="已发货")
    bad_key = c.record_tool_call(name="refund_apply", args={}, agent_name="order_service")
    c.finish_tool_call(bad_key, error="工具执行超时")
    assert (c.tool_call_count, c.tool_error_count) == (2, 1)
    assert c.tool_names == ["order_query", "refund_apply"]
    assert _span(c, SPAN_TOOL)["status"] == "ok"
    error_spans = [s for s in c.spans if s["type"] == SPAN_TOOL and s["status"] == "error"]
    assert error_spans[0]["attributes"]["error"] == "工具执行超时"


def test_record_outcome_fields(monkeypatch):
    # error_message 会被 clip_payload 裁切，断言正文前必须显式打开载荷存储
    monkeypatch.setattr(settings, "trace_store_payload", True)
    c = _collector()
    c.record_outcome(OUTCOME_HANDOVER, reason="low_confidence")
    assert c.outcome == OUTCOME_HANDOVER and c.handover_reason == "low_confidence"

    c2 = _collector()
    c2.record_outcome(OUTCOME_ERROR, code=5006, message="Agent 未注册")
    assert (c2.error_code, c2.error_message) == (5006, "Agent 未注册")

    c3 = _collector()
    c3.record_outcome(OUTCOME_ANSWERED)
    assert c3.handover_reason is None and c3.error_code is None


# ---------------------------------------------------------------- 采样
def test_should_persist_forces_abnormal_outcomes(monkeypatch):
    monkeypatch.setattr(settings, "trace_sample_rate", 0.0)
    for outcome, kw in [(OUTCOME_HANDOVER, {"reason": "low_confidence"}), (OUTCOME_ERROR, {"code": 5001})]:
        c = _collector()
        c.record_tool_call(name="t", args={})
        c.finish_tool_call("", result=None)
        c.record_outcome(outcome, **kw)
        assert c.should_persist() is True


def test_should_persist_keeps_tool_errors_even_when_answered(monkeypatch):
    monkeypatch.setattr(settings, "trace_sample_rate", 0.0)
    c = _collector()
    key = c.record_tool_call(name="t", args={})
    c.finish_tool_call(key, error="boom")
    c.record_outcome(OUTCOME_ANSWERED)
    assert c.should_persist() is True


def test_should_persist_sampling_respects_rate(monkeypatch):
    monkeypatch.setattr(settings, "trace_sample_rate", 0.0)
    kept = [ _answered_collector() for _ in range(50) ]
    assert any(not c.should_persist() for c in kept)

    monkeypatch.setattr(settings, "trace_sample_rate", 1.0)
    assert all(c.should_persist() for c in [_answered_collector() for _ in range(50)])


def _answered_collector() -> TraceCollector:
    c = _collector()
    c.record_outcome(OUTCOME_ANSWERED)
    return c


# ---------------------------------------------------------------- 建表DDL 与 ORM 对齐
def test_ddl_and_orm_columns_stay_in_sync():
    """采集器新增维度时容易只改 ORM 忘改 init_db.sql，真到写库才 500。"""
    import re
    from pathlib import Path

    from app.models.entities import RequestTrace

    sql_path = Path(__file__).resolve().parents[2] / "scripts" / "init_db.sql"
    block = re.search(
        r"CREATE TABLE (?:IF NOT EXISTS )?request_traces \((.*?)\n\);",
        sql_path.read_text(encoding="utf-8"),
        re.S,
    )
    assert block, "init_db.sql 里找不到 request_traces 建表语句"
    ddl_cols = {
        line.strip().split()[0]
        for line in block.group(1).splitlines()
        if line.strip() and not line.strip().startswith("--")
    }
    orm_cols = {c.name for c in RequestTrace.__table__.columns}
    assert orm_cols == ddl_cols

    # 采集器产出的每一列都必须能落进表里
    row = _collector().to_row()
    missing = set(row) - orm_cols
    assert not missing, f"to_row 产出了表里没有的列: {missing}"


# ---------------------------------------------------------------- 落库行
def test_to_row_is_flat_and_cards_copy_lists():
    c = _collector()
    c.add_unique("agent_names", "order_service")
    c.response_chars = 120
    row = c.to_row()
    assert row["trace_id"] == "t-1"
    assert row["session_id"] == "s-1" and row["user_id"] == "u-1"
    assert row["agent_names"] == ["order_service"]
    assert row["total_latency_ms"] is None
    # 返回的必须是副本，后续继续采集不能改写已交给 Repo 的行
    row["agent_names"].append("hacked")
    assert c.agent_names == ["order_service"]
