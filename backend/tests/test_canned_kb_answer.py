"""知识库来源的固定话术：检索 → 裁剪 → 固定框架拼接（不经过模型）。

固定话术的价值是"措辞可控 + 延迟低"，所以这里守三件事：
- 拼装格式固定（序号 + 去重 + 长度上限），运营改文案后仍然可预期；
- 任何检索失败都返回空文本由调用方降级，不向客户抛错；
- 只检索"存在且启用"的知识库，配错库不能静默变成"不知道"。
"""
from types import SimpleNamespace

import pytest

from app.agents.canned_scripts import CannedScript
from app.schemas.scene import SceneRuntime
from app.services import canned_answer as ca


def _kb(namespace="cs_order", is_active=True):
    return SimpleNamespace(namespace=namespace, is_active=is_active)


def _script(text="", namespaces=("cs_order",)):
    return CannedScript(
        key="identity",
        category="identity",
        text=text,
        answer_source="kb",
        kb_namespaces=tuple(namespaces),
    )


def _scene(code="mall_order", name="商城订单", agents=("order_service",)):
    return SimpleNamespace(code=code, name=name, agent_names=agents)


def _stub_kbs(monkeypatch, rows):
    async def fake_list_all():
        return rows

    # 启用库清单有 10s 进程内缓存：换一份 stub 前必须清掉上一用例的缓存
    ca._reset_base_cache()
    monkeypatch.setattr(ca.KnowledgeBaseRepo, "list_all", fake_list_all)


def _stub_search(monkeypatch, hits_by_ns=None, fail=()):
    calls: list[dict] = []
    hits_by_ns = hits_by_ns or {}

    async def fake_search(self, namespace, query, **kw):
        calls.append({"namespace": namespace, "query": query, **kw})
        if namespace in fail:
            raise RuntimeError("qdrant down")
        return hits_by_ns.get(namespace, [])

    monkeypatch.setattr(ca.RagService, "search", fake_search)
    return calls


# ===== 纯函数：清洗与拼装 =====
def test_clean_collapses_whitespace_and_truncates():
    assert ca._clean("  a\n\n  b \t c ") == "a b c"
    long_text = "字" * (ca.MAX_CHUNK_CHARS + 20)
    cleaned = ca._clean(long_text)
    assert len(cleaned) == ca.MAX_CHUNK_CHARS + 1
    assert cleaned.endswith("…")


def test_merge_orders_by_score_dedupes_and_caps():
    hits = [
        {"content": "低分片段", "score": 0.1},
        {"content": "高分片段", "score": 0.9},
        {"content": "  高分片段 ", "score": 0.8},  # 清洗+小写后重复
        {"content": "", "score": 0.7},
        {"content": "中分片段", "score": 0.5},
        {"content": "第四段", "score": 0.4},
    ]
    lines = ca._merge(hits)
    assert lines == ["1. 高分片段", "2. 中分片段", "3. 第四段"]
    # 最多 3 条：再多会变成把知识库整篇贴给客户
    assert len(lines) == ca.KB_TOP_K


def test_merge_respects_total_char_budget(monkeypatch):
    """总长上限是兜底：条数上限调大时不能让回复变成一整页贴文"""
    monkeypatch.setattr(ca, "MAX_TOTAL_CHARS", 100)
    big = "长" * ca.MAX_CHUNK_CHARS
    lines = ca._merge([{"content": big, "score": 0.9}, {"content": big, "score": 0.8}])
    assert len(lines) == 0


def test_three_max_chunks_fit_within_budget():
    # 默认参数下三个满长片段必须在总长上限内，否则第 3 条会被静默丢掉
    assert ca.KB_TOP_K * (ca.MAX_CHUNK_CHARS + 4) <= ca.MAX_TOTAL_CHARS


# ===== 检索：没有可用知识库 =====
@pytest.mark.asyncio
async def test_no_active_namespace_returns_no_namespace(monkeypatch):
    _stub_kbs(monkeypatch, [_kb(is_active=False)])
    _stub_search(monkeypatch)

    answer = await ca.answer_from_kb(_scene(), _script(), SceneRuntime(), "你是谁")
    assert answer.text == ""
    assert answer.status == "no_namespace"


@pytest.mark.asyncio
async def test_inactive_namespace_is_skipped(monkeypatch):
    _stub_kbs(monkeypatch, [_kb("cs_order", is_active=False), _kb("cs_it")])
    calls = _stub_search(monkeypatch, {"cs_it": [{"content": "IT 支持", "score": 0.9}]})

    answer = await ca.answer_from_kb(_scene(), _script(namespaces=("cs_order", "cs_it")), None, "报修")
    assert [c["namespace"] for c in calls] == ["cs_it"]
    assert answer.namespaces == ("cs_it",)
    assert "1. IT 支持" in answer.text


# ===== 检索：成功拼装 =====
@pytest.mark.asyncio
async def test_success_puts_intro_before_numbered_chunks(monkeypatch):
    _stub_kbs(monkeypatch, [_kb()])
    _stub_search(
        monkeypatch,
        {
            "cs_order": [
                {"content": "订单 30 分钟内发货", "score": 0.9},
                {"content": "支持 7 天无理由退货", "score": 0.8},
            ]
        },
    )

    answer = await ca.answer_from_kb(
        _scene(), _script(text="关于「{{scene_name}}」，我们查到："), None, "退货政策"
    )
    assert answer.status == "ok"
    assert answer.chunks == 2
    # 占位符在引导语里同样要渲染：同一份话术复制到不同场景才不串台
    assert answer.text.startswith("关于「商城订单」，我们查到：")
    assert "\n\n1. 订单 30 分钟内发货\n2. 支持 7 天无理由退货" in answer.text


@pytest.mark.asyncio
async def test_empty_intro_leaves_body_only(monkeypatch):
    _stub_kbs(monkeypatch, [_kb()])
    _stub_search(monkeypatch, {"cs_order": [{"content": "只有正文", "score": 0.9}]})

    answer = await ca.answer_from_kb(_scene(), _script(), None, "x")
    assert answer.text == "1. 只有正文"


@pytest.mark.asyncio
async def test_search_options_follow_runtime(monkeypatch):
    """场景配的 top_k 不用于话术（固定 3 条），但 min_score/超时必须跟随场景参数"""
    _stub_kbs(monkeypatch, [_kb()])
    calls = _stub_search(monkeypatch, {"cs_order": [{"content": "片段", "score": 0.9}]})
    runtime = SceneRuntime(rag_min_score=0.55, rag_timeout=20)

    await ca.answer_from_kb(_scene(), _script(), runtime, "x")
    assert calls[0]["min_score"] == 0.55
    # 话术对延迟敏感：即使场景配了 20 秒也只等 3 秒
    assert calls[0]["timeout"] == ca.KB_TIMEOUT_CAP
    assert calls[0]["top_k"] == ca.KB_TOP_K


# ===== 检索：降级 =====
@pytest.mark.asyncio
async def test_all_failures_report_error_and_empty_text(monkeypatch):
    _stub_kbs(monkeypatch, [_kb()])
    _stub_search(monkeypatch, fail=("cs_order",))

    answer = await ca.answer_from_kb(_scene(), _script(), None, "x")
    assert answer.text == ""
    assert answer.status == "error"
    assert answer.chunks == 0
    assert answer.notes and "qdrant down" in answer.notes[0]


@pytest.mark.asyncio
async def test_zero_hits_report_empty(monkeypatch):
    _stub_kbs(monkeypatch, [_kb()])
    _stub_search(monkeypatch, {"cs_order": []})

    answer = await ca.answer_from_kb(_scene(), _script(text="兜底引导"), None, "x")
    assert answer.status == "empty"
    # 零命中时正文不参与拼装：交给调用方决定是否用 reply_text 兜底
    assert answer.text == ""


@pytest.mark.asyncio
async def test_partial_failure_keeps_hits_and_notes(monkeypatch):
    _stub_kbs(monkeypatch, [_kb("cs_order"), _kb("cs_it")])
    _stub_search(
        monkeypatch,
        {"cs_order": [{"content": "订单有货", "score": 0.9}]},
        fail=("cs_it",),
    )

    answer = await ca.answer_from_kb(
        _scene(), _script(namespaces=("cs_order", "cs_it")), None, "x"
    )
    assert answer.status == "ok"
    assert answer.chunks == 1
    assert answer.notes and "cs_it" in answer.notes[0]
