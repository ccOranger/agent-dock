"""评测执行器的纯逻辑测试：全部用假观测，不对真实 LLM 发一个请求

覆盖三件事：
1. 用例集能加载、结构合法、判定维度齐全（回归闸的"闸"本身要可信）；
2. 每个检查点在"该通过/该失败/该跳过"三种情形下的行为（含采样导致的不进分母）；
3. 指标聚合、基线容差与报告渲染——尤其是验收要求的"改错 Agent 后意图准确率下降"。
"""
import json

import pytest

from app.schemas.eval import (
    SOURCE_DEMO_CORPUS,
    SOURCES,
    CaseResult,
    EvalCase,
    EvalExpect,
    EvalMetrics,
    EvalObservation,
    EvalRunSummary,
)
from app.services import eval_runner
from app.services.eval_runner import (
    CHECK_ANSWER_LENGTH,
    CHECK_CONFIDENCE,
    CHECK_EXECUTED,
    CHECK_FALLBACK,
    CHECK_HANDOVER,
    CHECK_INTENT,
    CHECK_INTENTS,
    CHECK_KEYWORDS,
    CHECK_MUST_NOT,
    CHECK_RAG,
    CHECK_RAG_EMPTY,
    CHECK_REJECTED,
    CHECK_TOOLS,
    CHECK_TOOLS_LIMIT,
    REGRESSION_ABS,
    FixtureError,
    aggregate,
    diff_metrics,
    fingerprint,
    has_regression,
    load_cases,
    percentile,
    render_report,
    score_case,
)


def make_case(case_id: str = "c-1", *, scene: str = "customer_service", expect: dict | None = None, **kw) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        scene=scene,
        query=kw.pop("query", "帮我查一下订单 SO20240001 的物流"),
        expect=EvalExpect(**(expect or {})),
        **kw,
    )


def make_obs(case_id: str = "c-1", **kw) -> EvalObservation:
    base = {
        "case_id": case_id,
        "executed": True,
        "http_status": 200,
        "trace_id": "t-" + case_id,
        "session_id": "s-1",
        "intents": ["order_service"],
        "confidence": 0.95,
        "tools": ["query_logistics"],
        "rag_chunks": 3,
        "rag_namespaces": ["cs_default"],
        "tokens": 1200,
        "first_token_ms": 800,
        "total_ms": 2400,
        "answer": "已为您查到物流信息，运单已发出",
    }
    base.update(kw)
    return EvalObservation(**base)


def score(expect: dict, obs: EvalObservation, *, case_id: str = "c-1") -> CaseResult:
    return score_case(make_case(case_id, expect=expect), obs)


def names(result: CaseResult) -> set[str]:
    return {c.name for c in result.checks}


# ---------------------------------------------------------------- 用例集


def test_repo_cases_load_and_are_unique():
    cases = load_cases()
    assert len(cases) >= 40, "首批用例集应达到排期要求的 30–50 条规模"
    assert len({c.case_id for c in cases}) == len(cases)
    scenes = {c.scene for c in cases}
    assert {"customer_service", "default"} <= scenes


def test_repo_cases_each_expect_something():
    # 没有期望的用例只会产出"跑通了"这一条结论，对回归没有信息量
    for case in load_cases():
        assert case.expect.model_dump(exclude_defaults=True), f"{case.case_id} 没有任何期望项"
        assert case.source in SOURCES, f"{case.case_id} 来源标注非法"
        assert case.query.strip()


def test_repo_cases_cover_every_dimension():
    cases = load_cases()
    assert sum(1 for c in cases if c.expect.agent or c.expect.agents_any) >= 10
    assert sum(1 for c in cases if c.expect.tools_any or c.expect.tools_all) >= 5
    assert sum(1 for c in cases if c.expect.expect_rag) >= 5
    assert sum(1 for c in cases if c.expect.min_intents or c.expect.intents_include) >= 2
    assert sum(1 for c in cases if c.expect.fallback is not None) >= 2
    assert sum(1 for c in cases if c.expect.must_not) >= 3
    assert sum(1 for c in cases if c.expect.rejected) >= 1
    assert sum(1 for c in cases if c.setup) >= 1, "追问续接类用例需要 setup 铺垫轮"


def test_repo_cases_source_annotation_is_replaceable_with_real_traces():
    """来源分层必须可审计：现场有真实链路后按 60/40 回填 trace_sample。"""
    cases = load_cases()
    dist = {s: sum(1 for c in cases if c.source == s) for s in SOURCES}
    assert dist[SOURCE_DEMO_CORPUS] >= 1 and dist["manual"] >= 1
    assert sum(dist.values()) == len(cases)


def test_filter_by_scene_and_tag():
    cs = load_cases(scene="customer_service")
    assert cs and all(c.scene == "customer_service" for c in cs)
    multi = load_cases(tag="多意图")
    assert multi and all("多意图" in c.tags for c in multi)
    with pytest.raises(FixtureError):
        load_cases(scene="no_such_scene")


def test_duplicate_case_id_is_rejected(tmp_path):
    blob = {
        "scene": "s",
        "cases": [
            {"case_id": "a", "query": "q1", "expect": {"agent": "x"}},
            {"case_id": "a", "query": "q2", "expect": {"agent": "y"}},
        ],
    }
    (tmp_path / "s.json").write_text(json.dumps(blob), encoding="utf-8")
    with pytest.raises(FixtureError, match="重复"):
        load_cases(tmp_path)


def test_empty_case_file_is_rejected(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps({"scene": "s", "cases": []}), encoding="utf-8")
    with pytest.raises(FixtureError, match="没有任何用例"):
        load_cases(tmp_path)


def test_missing_dir_is_rejected(tmp_path):
    with pytest.raises(FixtureError, match="目录不存在"):
        load_cases(tmp_path / "nope")


# ---------------------------------------------------------------- 指纹


def test_fingerprint_ignores_notes_and_tags():
    a = [make_case("c-1", expect={"agent": "x"}, tags=["旧标签"], note="旧说明")]
    b = [make_case("c-1", expect={"agent": "x"}, tags=["新标签"], note="改过说明")]
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_follows_judgement_fields():
    a = [make_case("c-1", expect={"agent": "x"})]
    b = [make_case("c-1", expect={"agent": "y"})]
    c = [make_case("c-2", expect={"agent": "x"})]
    assert fingerprint(a) != fingerprint(b)
    assert fingerprint(a) != fingerprint(c)
    assert fingerprint(a) == fingerprint(list(reversed(a)))


def test_fingerprint_order_independent():
    one = [make_case("c-1", expect={"agent": "x"}), make_case("c-2", expect={"agent": "y"})]
    two = [make_case("c-2", expect={"agent": "y"}), make_case("c-1", expect={"agent": "x"})]
    assert fingerprint(one) == fingerprint(two)


# ---------------------------------------------------------------- 打分：意图与工具


def test_intent_pass_and_fail():
    ok = score({"agent": "order_service"}, make_obs())
    assert ok.passed and CHECK_INTENT in names(ok)
    bad = score({"agent": "knowledge_qa"}, make_obs())
    assert not bad.passed and bad.failed_checks == [CHECK_INTENT]
    assert "主意图=order_service" in bad.checks[1].detail or "主意图" in bad.checks[-1].detail


def test_intent_agents_any_accepts_either():
    assert score({"agents_any": ["order_service", "knowledge_qa"]}, make_obs()).passed


def test_intent_fails_when_no_route_event():
    """固定话术本轮没有 route 事件：写了 agent 期望就该判失败，而不是静默通过。"""
    result = score({"agent": "order_service"}, make_obs(intents=[]))
    assert not result.passed and result.failed_checks == [CHECK_INTENT]


def test_multi_intent_requires_all_declared_agents():
    obs = make_obs(intents=["order_service", "complaint_handler"])
    assert score({"min_intents": 2, "intents_include": ["order_service", "complaint_handler"]}, obs).passed
    single = make_obs(intents=["order_service"])
    result = score({"min_intents": 2, "intents_include": ["order_service", "complaint_handler"]}, single)
    assert not result.passed and CHECK_INTENTS in result.failed_checks


def test_confidence_skipped_when_not_observed():
    """直连与固定话术没有置信度：跳过而不是判 0 分。"""
    hit = score({"min_confidence": 0.5}, make_obs(confidence=0.9))
    assert hit.passed
    miss = score({"min_confidence": 0.5}, make_obs(confidence=0.2))
    assert not miss.passed and miss.failed_checks == [CHECK_CONFIDENCE]
    none = score({"min_confidence": 0.5}, make_obs(confidence=None))
    assert none.passed and none.skipped == [CHECK_CONFIDENCE]


def test_tools_all_and_any():
    obs = make_obs(tools=["query_order", "query_logistics"])
    assert score({"tools_all": ["query_order", "query_logistics"]}, obs).passed
    missing = score({"tools_all": ["modify_order"]}, obs)
    assert not missing.passed and missing.failed_checks == [CHECK_TOOLS]
    assert score({"tools_any": ["modify_order", "query_order"]}, obs).passed
    miss_any = score({"tools_any": ["escalate"]}, obs)
    assert not miss_any.passed and miss_any.failed_checks == [CHECK_TOOLS]


def test_max_tools_is_policy_fence():
    assert score({"max_tools": 0}, make_obs(tools=[])).passed
    result = score({"max_tools": 0}, make_obs(tools=["modify_order"]))
    assert not result.passed and result.failed_checks == [CHECK_TOOLS_LIMIT]


def test_tool_error_count_does_not_change_passed():
    """工具报错属于链路问题，已由工具命中率与链路页暴露，不重复计入用例结论。"""
    assert score({"tools_any": ["query_logistics"]}, make_obs(tool_error_count=1)).passed


# ---------------------------------------------------------------- 打分：RAG / 结局 / 文本


def test_rag_hit_namespace_and_empty():
    assert score({"expect_rag": True}, make_obs(rag_chunks=2)).passed
    assert score({"expect_rag": True, "rag_namespace": "cs_default"}, make_obs()).passed
    assert not score({"expect_rag": True, "rag_namespace": "other_ns"}, make_obs()).passed
    assert not score({"expect_rag": True}, make_obs(rag_chunks=0)).passed
    assert score({"rag_empty": True}, make_obs(rag_chunks=0)).passed
    assert not score({"rag_empty": True}, make_obs(rag_chunks=4)).passed


def test_rag_unobserved_is_skipped_not_failed():
    """链路被采样丢弃时 RAG 无从回读：记 skipped，不冤枉成效果退化。"""
    result = score({"expect_rag": True, "rag_namespace": "cs_default"}, make_obs(rag_chunks=None))
    assert result.passed
    assert CHECK_RAG in result.skipped and not result.failed_checks
    empty = score({"rag_empty": True}, make_obs(rag_chunks=None))
    assert CHECK_RAG_EMPTY in empty.skipped


def test_handover_and_fallback_flags():
    hit = score({"handover": True}, make_obs(handover=True, handover_reason="low_confidence"))
    assert hit.passed and CHECK_HANDOVER in names(hit)
    miss = score({"handover": True}, make_obs())
    assert not miss.passed and miss.failed_checks == [CHECK_HANDOVER]
    assert score({"fallback": True}, make_obs(fallback=True)).passed
    fb = score({"fallback": False}, make_obs(fallback=True))
    assert not fb.passed and fb.failed_checks == [CHECK_FALLBACK]


def test_keywords_any_all_and_must_not():
    obs = make_obs(answer="退货请在 7 天内申请，运费由我们承担")
    assert score({"keywords_any": ["退货", "退款"]}, obs).passed
    miss = score({"keywords_any": ["发票"]}, obs)
    assert not miss.passed and miss.failed_checks == [CHECK_KEYWORDS]
    assert score({"keywords_all": ["7 天", "运费"]}, obs).passed
    assert not score({"keywords_all": ["7 天", "包邮"]}, obs).passed
    assert score({"must_not": ["无条件赔偿"]}, obs).passed
    leaked = score({"must_not": ["运费"]}, obs)
    assert not leaked.passed and leaked.failed_checks == [CHECK_MUST_NOT]
    assert "出现禁词" in leaked.checks[-1].detail


def test_max_answer_chars():
    assert score({"max_answer_chars": 5000}, make_obs()).passed
    assert not score({"max_answer_chars": 5}, make_obs()).passed
    assert CHECK_ANSWER_LENGTH in names(score({"max_answer_chars": 5000}, make_obs()))


def test_transfer_human_case_only_checks_action():
    """客户要求转人工：不校验承接方，只校验有没有升级动作。"""
    case = make_case("c-x", expect={"tools_any": ["escalate", "update_ticket"]})
    assert score_case(case, make_obs(tools=["escalate"])).passed


# ---------------------------------------------------------------- 打分：执行失败与拒答


def test_execution_failure_fails_everything_and_explains():
    result = score({"agent": "order_service"}, make_obs(executed=False, http_status=500, exec_error="HTTP 500"))
    assert not result.passed
    assert result.failed_checks == [CHECK_EXECUTED]
    assert "HTTP 500" in result.checks[0].detail


def test_rejected_case_passes_on_entry_block():
    """注入命中入口网关：返回 4xx 就是预期结局，不再叠 executed 失败。"""
    obs = make_obs(executed=False, http_status=400, error_code=4003, exec_error="输入包含不安全内容", answer="")
    result = score({"rejected": True}, obs)
    assert result.passed
    assert result.failed_checks == []
    assert CHECK_REJECTED in names(result)


def test_rejected_case_fails_when_input_reached_model():
    result = score({"rejected": True}, make_obs())
    assert not result.passed and result.failed_checks == [CHECK_REJECTED]


def test_mid_stream_error_is_not_a_valid_block():
    """流内 error（HTTP 200 + SSE）是服务故障，不能被算成"安全网关拦下了注入"。"""
    obs = make_obs(executed=False, http_status=200, error_code=5001, exec_error="服务暂时不可用")
    assert not score({"rejected": True}, obs).passed


def test_rejected_false_requires_successful_run():
    assert score({"rejected": False}, make_obs()).passed
    assert not score({"rejected": False}, make_obs(executed=False, http_status=403)).passed


# ---------------------------------------------------------------- 聚合


def results_of(pairs: list[tuple[dict, EvalObservation]]) -> list[CaseResult]:
    return [score_case(make_case(f"c-{i}", expect=e), o) for i, (e, o) in enumerate(pairs)]


def test_aggregate_denominators_exclude_unrelated_and_unobserved():
    m = aggregate(
        results_of(
            [
                ({"agent": "order_service"}, make_obs(case_id="c-0")),
                # 期望错 Agent：进意图分母且算不中
                ({"agent": "knowledge_qa"}, make_obs(case_id="c-1")),
                # 没写 agent 期望：不进意图分母
                ({"keywords_any": ["物流"]}, make_obs(case_id="c-2")),
                # RAG 观测不到：不进 RAG 分母，单列 unobserved
                ({"expect_rag": True}, make_obs(case_id="c-3", rag_chunks=None)),
                ({"expect_rag": True}, make_obs(case_id="c-4", rag_chunks=5)),
            ]
        )
    )
    assert m.total == 5
    assert m.intent_accuracy.denominator == 2
    assert m.intent_accuracy.numerator == 1
    # 没写这项期望的用例与指标完全无关，不能被当成"不可观测"
    assert m.intent_accuracy.unobserved == 0
    assert m.tool_hit_rate.denominator == 0
    assert m.tool_hit_rate.unobserved == 0
    assert m.rag_hit_rate.denominator == 1
    assert m.rag_hit_rate.unobserved == 1
    assert m.pass_rate.denominator == 5


def test_unexecuted_case_makes_its_expectations_unobserved():
    """本轮根本没跑到路由：写了意图期望的用例记作不可观测，不混进分母拉低准确率。"""
    m = aggregate(
        results_of(
            [
                ({"agent": "order_service"}, make_obs(case_id="c-0", executed=False, http_status=503)),
                ({"agent": "order_service"}, make_obs(case_id="c-1")),
            ]
        )
    )
    assert m.intent_accuracy.denominator == 1
    assert m.intent_accuracy.numerator == 1
    assert m.intent_accuracy.unobserved == 1
    assert m.errors == 1


def test_planned_checks_covers_every_expectation_field():
    """每个期望字段都要被 planned_checks 认得，否则新加维度不会进指标。"""
    fields = {
        "agent": {"agent": "x"},
        "agents_any": {"agents_any": ["x"]},
        "min_intents": {"min_intents": 2},
        "intents_include": {"intents_include": ["x"]},
        "tools_all": {"tools_all": ["t"]},
        "tools_any": {"tools_any": ["t"]},
        "expect_rag": {"expect_rag": True},
        "rag_namespace": {"rag_namespace": "ns"},
        "rag_empty": {"rag_empty": True},
        "handover": {"handover": True},
        "fallback": {"fallback": True},
        "min_confidence": {"min_confidence": 0.5},
        "keywords_any": {"keywords_any": ["k"]},
        "keywords_all": {"keywords_all": ["k"]},
        "must_not": {"must_not": ["k"]},
        "max_answer_chars": {"max_answer_chars": 10},
        "max_tools": {"max_tools": 0},
        "rejected": {"rejected": True},
    }
    declared = set(EvalExpect.model_fields) - {"agent", "agents_any"}
    assert set(fields) >= declared, f"新期望字段没进 planned_checks：{declared - set(fields)}"
    for value in fields.values():
        assert eval_runner.planned_checks(EvalExpect(**value)), f"{value} 没产生任何计划检查点"
    assert eval_runner.planned_checks(EvalExpect()) == []


def test_aggregate_pass_fail_and_error_counts():
    m = aggregate(
        results_of(
            [
                ({"agent": "order_service"}, make_obs(case_id="c-0")),
                ({"agent": "knowledge_qa"}, make_obs(case_id="c-1")),
                # 该被拦下的注入：没跑通但不算传输故障
                ({"rejected": True}, make_obs(case_id="c-2", executed=False, http_status=400, error_code=4003)),
                ({"agent": "x"}, make_obs(case_id="c-3", executed=False, http_status=503, exec_error="down")),
            ]
        )
    )
    assert (m.passed, m.failed, m.errors) == (2, 2, 1)


def test_aggregate_fallback_handover_and_latency():
    m = aggregate(
        results_of(
            [
                ({}, make_obs(case_id="c-0", fallback=True, first_token_ms=1000, total_ms=2000, tokens=100)),
                ({}, make_obs(case_id="c-1", handover=True, first_token_ms=2000, total_ms=4000, tokens=300)),
                ({}, make_obs(case_id="c-2", first_token_ms=3000, total_ms=6000, tokens=200)),
                # 没跑通的轮次不进延迟与 token 统计
                ({}, make_obs(case_id="c-3", executed=False, total_ms=9000)),
            ]
        )
    )
    assert m.fallback_rate.value == pytest.approx(1 / 3)
    assert m.handover_rate.numerator == 1
    assert m.p95_first_token_ms.denominator == 3
    assert m.avg_tokens.value == pytest.approx(200)
    assert m.total_tokens == 600
    assert m.avg_total_ms.value == pytest.approx(4000)


def test_percentile_nearest_rank():
    assert percentile([], 95) is None
    assert percentile([100], 95) == 100
    assert percentile([100, 200, 300], 95) == 300
    assert percentile([100, 200, 300], 50) == 200


# ---------------------------------------------------------------- 基线对比


def metrics_pair(**overrides) -> dict:
    """造一份指标字典用于 diff 测试：只关心 value，其余字段沿用默认。"""
    m = EvalMetrics()
    for name, value in overrides.items():
        getattr(m, name).value = value
    return m.as_dict()


def test_diff_ratio_tolerance_is_absolute_points():
    base = metrics_pair(pass_rate=0.90, intent_accuracy=0.90)
    same = metrics_pair(pass_rate=0.885, intent_accuracy=0.87)
    diffs = _by_name(diff_metrics(same, base, current_hash="h", baseline_hash="h"))
    # 1.5 个百分点在容差内，3 个百分点算退化
    assert not diffs["pass_rate"].regressed
    assert diffs["intent_accuracy"].regressed
    assert any(d.regressed for d in diff_metrics(same, base, current_hash="h", baseline_hash="h"))
    assert "容差" in diffs["intent_accuracy"].note
    assert REGRESSION_ABS == 0.02


def test_diff_latency_tolerance_is_relative():
    base = metrics_pair(p95_first_token_ms=1000, avg_tokens=1000)
    within = metrics_pair(p95_first_token_ms=1140, avg_tokens=1000)
    beyond = metrics_pair(p95_first_token_ms=1300, avg_tokens=1000)
    assert not has_regression(diff_metrics(within, base, current_hash="h", baseline_hash="h"))
    assert has_regression(diff_metrics(beyond, base, current_hash="h", baseline_hash="h"))


def test_diff_lower_is_better_direction():
    base = metrics_pair(handover_rate=0.10)
    worse = metrics_pair(handover_rate=0.30)
    better = metrics_pair(handover_rate=0.02)
    assert _by_name(diff_metrics(worse, base, current_hash="h", baseline_hash="h"))["handover_rate"].regressed
    assert not _by_name(diff_metrics(better, base, current_hash="h", baseline_hash="h"))["handover_rate"].regressed


def _by_name(diffs) -> dict:
    return {d.name: d for d in diffs}


def test_diff_uncomparable_when_fixture_changed():
    base = metrics_pair(pass_rate=0.90)
    cur = metrics_pair(pass_rate=0.50)
    diffs = diff_metrics(cur, base, current_hash="new", baseline_hash="old")
    assert all(not d.regressed for d in diffs)
    assert all(not d.comparable for d in diffs if d.name == "pass_rate")
    assert any("不可直接比较" in d.note for d in diffs)


def test_diff_without_baseline_is_not_compared():
    diffs = diff_metrics(metrics_pair(pass_rate=0.5), None, current_hash="h")
    assert diffs
    assert all(not d.regressed and d.baseline is None for d in diffs)
    assert "未提供基线" in diffs[0].note


def test_wrong_agent_expectation_lowers_intent_accuracy():
    """验收项"把某业务 Agent 提示词改错后报告能显示意图准确率下降"的等价证明：
    承接方从 order_service 变成兜底，意图准确率必须掉，并被判定退化。"""
    cases = [
        make_case("c-0", expect={"agent": "order_service"}),
        make_case("c-1", expect={"agent": "order_service"}),
        make_case("c-2", expect={"agent": "knowledge_qa"}),
        make_case("c-3", expect={"agent": "knowledge_qa"}),
    ]
    healthy = aggregate(
        [
            score_case(c, make_obs(case_id=c.case_id, intents=[n]))
            for c, n in zip(cases, ["order_service", "order_service", "knowledge_qa", "knowledge_qa"], strict=True)
        ]
    )
    broken = aggregate(
        [
            score_case(c, make_obs(case_id=c.case_id, intents=["complaint_handler"]))
            for c in cases
        ]
    )
    assert healthy.intent_accuracy.value == 1.0
    assert broken.intent_accuracy.value == 0.0
    diffs = diff_metrics(broken.as_dict(), healthy.as_dict(), current_hash="h", baseline_hash="h")
    intent = next(d for d in diffs if d.name == "intent_accuracy")
    assert intent.regressed and intent.delta == -1.0
    assert has_regression(diffs)


# ---------------------------------------------------------------- 报告


def build_summary(with_baseline: bool = True, **obs_overrides) -> EvalRunSummary:
    obs = make_obs(**obs_overrides)
    results = [score_case(make_case("c-1", expect={"agent": "knowledge_qa"}), obs)]
    metrics = aggregate(results)
    diffs = (
        diff_metrics(
            metrics.as_dict(),
            metrics_pair(pass_rate=1.0, intent_accuracy=1.0),
            current_hash="abc",
            baseline_hash="abc",
        )
        if with_baseline
        else []
    )
    return EvalRunSummary(
        run_tag="w4-验收",
        scene="customer_service",
        base_url="http://127.0.0.1:8000",
        model_name="some-model",
        fixture_dir="backend/tests/fixtures/eval",
        fixture_hash="abc",
        concurrency=2,
        trace_readback=True,
        started_at="2026-10-27 09:00:00",
        finished_at="2026-10-27 09:02:00",
        duration_ms=120000,
        metrics=metrics,
        results=results,
        diffs=diffs,
        baseline_hash="abc",
        baseline_tag="baseline",
        baseline_at="2026-10-26 09:00:00",
    )


def test_report_has_five_sections_and_conclusion():
    text = render_report(build_summary())
    for heading in (
        "# 评测报告：w4-验收",
        "## 1. 结论",
        "## 2. 指标与基线对比",
        "## 3. 指标口径与样本数",
        "## 4. 失败用例（1）",
        "## 5. 全量用例",
    ):
        assert heading in text
    assert "退化项" in text
    assert "意图准确率" in text
    assert "| 意图准确率 | 0.0%" in text
    assert "用例集进仓库" in text


def test_report_marks_regression_and_clean_run_reports_no_regression():
    assert "本次变更需要解释或回滚" in render_report(build_summary())
    clean = render_report(build_summary(intents=["knowledge_qa"]))
    assert "无指标退化" in clean


def test_report_without_baseline_says_so():
    text = render_report(build_summary(with_baseline=False))
    assert "未提供基线" in text
    assert "_本次未指定基线，无对比数据。_" in text


def test_report_failure_detail_carries_trace_id_and_excerpt():
    summary = build_summary()
    failure = summary.failures()[0]
    assert failure.trace_id == "t-c-1"
    assert failure.reason == CHECK_INTENT
    text = render_report(summary)
    assert "t-c-1" in text
    assert "后台链路页" in text


def test_detail_rows_are_compact_and_complete():
    rows = build_summary().as_detail_rows()
    assert len(rows) == 1
    row = rows[0]
    for key in ("case_id", "passed", "failed", "skipped", "intents", "tools", "rag_chunks", "tokens", "trace_id"):
        assert key in row
    assert len(row["answer_excerpt"]) <= eval_runner.EXCERPT_CHARS
