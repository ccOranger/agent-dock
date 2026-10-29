"""评测执行器的纯逻辑：用例集加载、打分、指标聚合、基线对比、Markdown 报告

设计约束：本模块**不碰 HTTP、不碰数据库**。真实链路的采集在 ``scripts/eval.py``
（打 ``POST /chat/stream``、回读 request_traces / chat_events），采集结果换成
``EvalObservation`` 后回到这里打分。这样打分逻辑可以用假观测全覆盖测试，
不必等一台装了模型与数据库的机器。

指标口径（与 docs/evaluation.md 一致，改动要同步文档）：
- 分母只算"写了这类期望"的用例；写了但观测不到的单列 unobserved，没写的完全不相关；
- 比率类指标退化容差 2 个百分点，耗时/token 类容差 15%（模型本身的波动就在这个量级）；
- 用例集指纹不同则不可比：报告里标注，不判退化。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.schemas.eval import (
    SOURCE_MANUAL,
    CaseResult,
    EvalCase,
    EvalCaseFile,
    EvalCheck,
    EvalExpect,
    EvalMetrics,
    EvalObservation,
    EvalRunSummary,
    MetricDiff,
    MetricPoint,
)

# 用例集默认位置：随测试资源一起进仓库，评审后才能改
DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "eval"

# 检查点名称：aggregate() 按这些名字归入对应指标分母，改名要两边同步
CHECK_EXECUTED = "executed"
CHECK_INTENT = "intent"
CHECK_INTENTS = "intents"
CHECK_CONFIDENCE = "confidence"
CHECK_TOOLS = "tools"
CHECK_RAG = "rag"
CHECK_RAG_EMPTY = "rag_empty"
CHECK_HANDOVER = "handover"
CHECK_FALLBACK = "fallback"
CHECK_KEYWORDS = "keywords"
CHECK_MUST_NOT = "must_not"
CHECK_ANSWER_LENGTH = "answer_length"
CHECK_TOOLS_LIMIT = "tools_limit"
CHECK_REJECTED = "rejected"

# 报告里应答摘录的截断长度：明细要给排查用，但不能把整段回答塞进库
EXCERPT_CHARS = 160

# 退化容差：比率类按绝对百分点，耗时/token 类按相对比例
REGRESSION_ABS = 0.02
REGRESSION_REL = 0.15

# (指标名, 展示名, 方向, 容差类型)：diff 与报告都按这个顺序输出
METRIC_SPECS: list[tuple[str, str, str, str]] = [
    ("pass_rate", "通过率", "up", "abs"),
    ("intent_accuracy", "意图准确率", "up", "abs"),
    ("tool_hit_rate", "工具命中率", "up", "abs"),
    ("rag_hit_rate", "RAG 命中率", "up", "abs"),
    ("fallback_rate", "兜底率", "down", "abs"),
    ("handover_rate", "转人工率", "down", "abs"),
    ("p95_first_token_ms", "P95 首字延迟(ms)", "down", "rel"),
    ("avg_total_ms", "平均总耗时(ms)", "down", "rel"),
    ("avg_tokens", "平均 token", "down", "rel"),
]


class FixtureError(ValueError):
    """用例集不合法：宁可不跑，也不要拿一份残缺的用例集给出"没退化"的结论。"""


# ---------------------------------------------------------------- 用例集加载


def load_case_files(fixture_dir: Path | None = None) -> list[EvalCaseFile]:
    """读取 ``<dir>/*.json`` 并校验结构；文件名与内部 scene 不一致时以用例为准。"""
    directory = Path(fixture_dir or DEFAULT_FIXTURE_DIR)
    if not directory.is_dir():
        raise FixtureError(f"用例集目录不存在：{directory}")
    files: list[EvalCaseFile] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            parsed = EvalCaseFile.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - 统一换成可操作的报错文案
            raise FixtureError(f"用例集 {path.name} 解析失败：{exc}") from exc
        if not parsed.cases:
            raise FixtureError(f"用例集 {path.name} 里没有任何用例")
        for case in parsed.cases:
            # 用例没写场景就是跟着文件走，省掉 30 份重复字段
            case.scene = case.scene or parsed.scene
        files.append(parsed)
    if not files:
        raise FixtureError(f"用例集目录为空：{directory}")
    return files


def load_cases(
    fixture_dir: Path | None = None,
    *,
    scene: str | None = None,
    tag: str | None = None,
) -> list[EvalCase]:
    """汇总用例并按场景/标签过滤；case_id 全局唯一（重复会让报告对不上号）。"""
    cases: list[EvalCase] = []
    for item in load_case_files(fixture_dir):
        for case in item.cases:
            if scene and case.scene != scene:
                continue
            if tag and tag not in case.tags:
                continue
            cases.append(case)
    seen: set[str] = set()
    dup = [c.case_id for c in cases if c.case_id in seen or seen.add(c.case_id)]
    if dup:
        raise FixtureError(f"case_id 重复：{sorted(set(dup))}")
    if not cases:
        raise FixtureError(
            f"过滤后没有用例（scene={scene or '全部'}, tag={tag or '全部'}），请检查用例集目录"
        )
    return cases


def fingerprint(cases: list[EvalCase]) -> str:
    """用例集内容指纹：只取影响判定的字段，改 note/标签不影响基线可比性。"""
    normalized = [
        {
            "case_id": c.case_id,
            "scene": c.scene,
            "query": c.query,
            "setup": c.setup,
            "expect": c.expect.model_dump(exclude_defaults=True),
        }
        for c in sorted(cases, key=lambda x: x.case_id)
    ]
    blob = json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


# ---------------------------------------------------------------- 打分


def _excerpt(text: str) -> str:
    return " ".join((text or "").split())[:EXCERPT_CHARS]


def _join(parts: list[str]) -> str:
    return "; ".join(p for p in parts if p)


def planned_checks(e: EvalExpect) -> list[str]:
    """这条用例写了哪些维度的期望（与能不能观测到无关）。

    打分与算指标共用这一份清单：分母只算"本来关心这项"的用例，
    避开把"用例没写 RAG 期望"当成"RAG 不可观测"。
    """
    names: list[str] = []
    if e.rejected is not None:
        names.append(CHECK_REJECTED)
    if e.agent or e.agents_any:
        names.append(CHECK_INTENT)
    if e.min_intents is not None or e.intents_include:
        names.append(CHECK_INTENTS)
    if e.min_confidence is not None:
        names.append(CHECK_CONFIDENCE)
    if e.tools_all or e.tools_any:
        names.append(CHECK_TOOLS)
    if e.max_tools is not None:
        names.append(CHECK_TOOLS_LIMIT)
    if e.expect_rag or e.rag_namespace:
        names.append(CHECK_RAG)
    if e.rag_empty:
        names.append(CHECK_RAG_EMPTY)
    if e.handover is not None:
        names.append(CHECK_HANDOVER)
    if e.fallback is not None:
        names.append(CHECK_FALLBACK)
    if e.keywords_any or e.keywords_all:
        names.append(CHECK_KEYWORDS)
    if e.must_not:
        names.append(CHECK_MUST_NOT)
    if e.max_answer_chars is not None:
        names.append(CHECK_ANSWER_LENGTH)
    return names


def score_case(case: EvalCase, obs: EvalObservation) -> CaseResult:
    """按期望项逐条判定；没写期望的维度不产生检查点。"""
    checks: list[EvalCheck] = []
    skipped: list[str] = []
    e = case.expect
    planned_list = planned_checks(e)
    planned = set(planned_list)

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append(EvalCheck(name=name, ok=ok, detail=detail))

    def finish() -> CaseResult:
        failed = [c.name for c in checks if not c.ok]
        return CaseResult(
            case_id=case.case_id,
            scene=case.scene,
            query=case.query,
            tags=list(case.tags),
            source=case.source or SOURCE_MANUAL,
            passed=not failed,
            checks=checks,
            failed_checks=failed,
            skipped=skipped,
            expected=planned_list,
            observation=obs,
            answer_excerpt=_excerpt(obs.answer or ""),
        )

    # 拒答类用例把"没跑通"当成预期结局，不能再叠一条 executed 失败让报告双报
    if CHECK_REJECTED in planned:
        # 入口拦截一定是非 200 的 HTTP 响应；流内 error 事件（200 + SSE）不算拦下，
        # 否则“模型/工具报错”会伪装成“安全网关生效”而把注入用例误判为通过
        blocked = (not obs.executed) and obs.http_status is not None and obs.http_status >= 400
        ok = blocked if e.rejected else obs.executed
        add(
            CHECK_REJECTED,
            ok,
            f"http={obs.http_status or '-'} code={obs.error_code or '-'} "
            f"error={obs.exec_error or '无'}；期望被拦={e.rejected}",
        )

    if not obs.executed:
        if CHECK_REJECTED not in planned:
            add(CHECK_EXECUTED, False, obs.exec_error or "请求未跑通")
        # 本轮没有真实应答，其余检查点无判定意义
        return finish()

    add(CHECK_EXECUTED, True)

    primary = obs.intents[0] if obs.intents else None

    if CHECK_INTENT in planned:
        allowed = set(e.agents_any)
        if e.agent:
            allowed.add(e.agent)
        ok = primary is not None and (primary == e.agent or primary in allowed)
        add(CHECK_INTENT, ok, f"主意图={primary or '无'}；期望={'|'.join(sorted(allowed))}")

    if CHECK_INTENTS in planned:
        problems: list[str] = []
        if e.min_intents is not None and len(obs.intents) < e.min_intents:
            problems.append(f"意图数={len(obs.intents)}<{e.min_intents}")
        missing = [name for name in e.intents_include if name not in obs.intents]
        if missing:
            problems.append(f"缺少意图={'|'.join(missing)}")
        add(
            CHECK_INTENTS,
            not problems,
            f"实际意图={'|'.join(obs.intents) or '无'}" + (f"；{_join(problems)}" if problems else ""),
        )

    if CHECK_CONFIDENCE in planned:
        if obs.confidence is None:
            # 直连场景与固定话术都没有置信度，不能当成 0 分判定
            skipped.append(CHECK_CONFIDENCE)
        else:
            add(
                CHECK_CONFIDENCE,
                obs.confidence >= e.min_confidence,
                f"置信度={obs.confidence:.2f} 下限={e.min_confidence:.2f}",
            )

    if CHECK_TOOLS in planned:
        called = set(obs.tools)
        missing = [t for t in e.tools_all if t not in called]
        hit_any = bool(set(e.tools_any) & called) if e.tools_any else True
        problems = []
        if missing:
            problems.append(f"未调用={'|'.join(missing)}")
        if not hit_any:
            problems.append(f"期望至少其一={'|'.join(e.tools_any)}")
        add(
            CHECK_TOOLS,
            not problems,
            f"实际工具={'|'.join(obs.tools) or '无'}" + (f"；{_join(problems)}" if problems else ""),
        )

    if CHECK_TOOLS_LIMIT in planned:
        add(
            CHECK_TOOLS_LIMIT,
            len(obs.tools) <= e.max_tools,
            f"工具调用数={len(obs.tools)} 上限={e.max_tools}（{'|'.join(obs.tools) or '无'}）",
        )

    if CHECK_RAG in planned:
        if obs.rag_chunks is None:
            skipped.append(CHECK_RAG)
        else:
            problems = []
            if e.expect_rag and obs.rag_chunks <= 0:
                problems.append("检索零命中")
            if e.rag_namespace and e.rag_namespace not in obs.rag_namespaces:
                problems.append(f"namespace 未命中 {e.rag_namespace}")
            add(
                CHECK_RAG,
                not problems,
                f"片段数={obs.rag_chunks}；ns={'|'.join(obs.rag_namespaces) or '无'}"
                + (f"；{_join(problems)}" if problems else ""),
            )

    if CHECK_RAG_EMPTY in planned:
        if obs.rag_chunks is None:
            skipped.append(CHECK_RAG_EMPTY)
        else:
            add(
                CHECK_RAG_EMPTY,
                obs.rag_chunks == 0,
                f"期望零命中，实际片段数={obs.rag_chunks}",
            )

    if CHECK_HANDOVER in planned:
        add(CHECK_HANDOVER, obs.handover == e.handover, f"转人工={obs.handover} 期望={e.handover}")
    if CHECK_FALLBACK in planned:
        add(CHECK_FALLBACK, obs.fallback == e.fallback, f"兜底={obs.fallback} 期望={e.fallback}")

    answer = obs.answer or ""
    if CHECK_KEYWORDS in planned:
        missing = [k for k in e.keywords_all if k not in answer]
        hit_any = any(k in answer for k in e.keywords_any) if e.keywords_any else True
        problems = []
        if missing:
            problems.append(f"缺少={'|'.join(missing)}")
        if not hit_any:
            problems.append(f"期望至少其一={'|'.join(e.keywords_any)}")
        add(CHECK_KEYWORDS, not problems, _join(problems))
    if CHECK_MUST_NOT in planned:
        leaked = [k for k in e.must_not if k in answer]
        add(CHECK_MUST_NOT, not leaked, f"出现禁词={'|'.join(leaked)}" if leaked else "")
    if CHECK_ANSWER_LENGTH in planned:
        add(
            CHECK_ANSWER_LENGTH,
            len(answer) <= e.max_answer_chars,
            f"长度={len(answer)} 上限={e.max_answer_chars}",
        )

    return finish()


# ---------------------------------------------------------------- 指标聚合


def percentile(values: list[float], pct: float) -> float | None:
    """最近邻法分位（样本少时不做插值：报告要能手工核对）。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    idx = int(round((pct / 100) * (len(ordered) - 1)))
    return float(ordered[max(0, min(idx, len(ordered) - 1))])


def _point(num: int = 0, den: int = 0, unobserved: int = 0) -> MetricPoint:
    return MetricPoint(
        value=(num / den) if den else None,
        numerator=num,
        denominator=den,
        unobserved=unobserved,
    )


def _check_state(result: CaseResult, names: tuple[str, ...]) -> bool | None:
    """用例在这些检查点上的合并状态：没产生任何一条 → None（无可判定的样本）。"""
    hits = [c.ok for c in result.checks if c.name in names]
    if not hits:
        return None
    return all(hits)


def _point_from(results: list[CaseResult], names: tuple[str, ...]) -> MetricPoint:
    """比率指标按"用例关心不关心这项"定分母：

    - 没写这类期望：与指标无关，既不进分母也不算不可观测；
    - 写了但没产生判定（链路被采样丢弃、或本轮根本没跑到这一步）：记 unobserved；
    - 产生了判定：进分母。

    把"没关心"当成"不可观测"会让报表里充满无关样本，指标一变就说不清原因。
    """
    num = den = unobserved = 0
    wanted = set(names)
    for r in results:
        if not wanted & set(r.expected):
            continue
        state = _check_state(r, names)
        if state is None:
            unobserved += 1
        else:
            den += 1
            num += 1 if state else 0
    return _point(num, den, unobserved)


def aggregate(results: list[CaseResult]) -> EvalMetrics:
    executed = [r for r in results if r.observation.executed]
    metrics = EvalMetrics(
        total=len(results),
        passed=sum(1 for r in results if r.passed),
        failed=sum(1 for r in results if not r.passed),
        # “没跑通”但本来就该被拦下的用例（注入/鉴权）不算执行故障
        errors=sum(1 for r in results if not r.observation.executed and not r.passed),
    )
    metrics.pass_rate = _point(metrics.passed, metrics.total)
    metrics.intent_accuracy = _point_from(results, (CHECK_INTENT, CHECK_INTENTS))
    metrics.tool_hit_rate = _point_from(results, (CHECK_TOOLS,))
    metrics.rag_hit_rate = _point_from(results, (CHECK_RAG, CHECK_RAG_EMPTY))

    metrics.fallback_rate = _point(
        sum(1 for r in executed if r.observation.fallback), len(executed)
    )
    metrics.handover_rate = _point(
        sum(1 for r in executed if r.observation.handover), len(executed)
    )

    first_tokens = [r.observation.first_token_ms for r in executed if r.observation.first_token_ms is not None]
    p95 = percentile([float(v) for v in first_tokens], 95)
    metrics.p95_first_token_ms = MetricPoint(
        value=p95, numerator=len(first_tokens), denominator=len(executed)
    )

    totals = [r.observation.total_ms for r in executed if r.observation.total_ms]
    metrics.avg_total_ms = MetricPoint(
        value=(sum(totals) / len(totals)) if totals else None,
        numerator=len(totals),
        denominator=len(executed),
    )

    tokens = [r.observation.tokens for r in executed if r.observation.tokens is not None]
    metrics.avg_tokens = MetricPoint(
        value=(sum(tokens) / len(tokens)) if tokens else None,
        numerator=len(tokens),
        denominator=len(executed),
        unobserved=len(executed) - len(tokens),
    )
    metrics.total_tokens = sum(tokens)
    return metrics


# ---------------------------------------------------------------- 基线对比


def _metric_value(metrics: dict, name: str) -> float | None:
    point = metrics.get(name) or {}
    return point.get("value") if isinstance(point, dict) else None


def diff_metrics(
    current: dict,
    baseline: dict | None,
    *,
    current_hash: str = "",
    baseline_hash: str = "",
) -> list[MetricDiff]:
    """逐指标对比：只有超出容差才标退化，避免把模型波动当成回归问题。"""
    comparable = bool(baseline) and (not baseline_hash or not current_hash or current_hash == baseline_hash)
    diffs: list[MetricDiff] = []
    for name, label, direction, kind in METRIC_SPECS:
        cur = _metric_value(current, name)
        base = _metric_value(baseline or {}, name)
        diff = MetricDiff(name=name, label=label, current=cur, baseline=base, direction=direction)
        if base is None or cur is None:
            diff.note = "基线或本次无该项数据" if baseline else "未提供基线"
            diffs.append(diff)
            continue
        delta = cur - base
        diff.delta = delta
        if not comparable:
            diff.comparable = False
            diff.note = "用例集指纹与基线不同，指标不可直接比较"
            diffs.append(diff)
            continue
        if kind == "abs":
            bad = delta < -REGRESSION_ABS if direction == "up" else delta > REGRESSION_ABS
            diff.regressed = bad
            if bad:
                diff.note = f"变化 {delta:+.1%}，超出 {REGRESSION_ABS:.0%} 容差"
        else:
            rel = delta / abs(base) if base else 0.0
            bad = rel > REGRESSION_REL if direction == "down" else rel < -REGRESSION_REL
            diff.regressed = bad
            if bad:
                diff.note = f"变化 {rel:+.0%}，超出 {REGRESSION_REL:.0%} 容差"
        diffs.append(diff)
    return diffs


def has_regression(diffs: list[MetricDiff]) -> bool:
    return any(d.regressed for d in diffs)


# ---------------------------------------------------------------- 报告


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _num(value: float | None) -> str:
    return "—" if value is None else (f"{value:.0f}" if value >= 1 else f"{value:.2f}")


def _frac(point: MetricPoint) -> str:
    if point.denominator == 0 and point.value is None:
        return "—"
    return f"{point.numerator}/{point.denominator}"


def render_report(summary: EvalRunSummary) -> str:
    """Markdown 报告：可直接作为交付验收附件（结论、口径、失败明细、全量结果）。"""
    m = summary.metrics
    lines: list[str] = []
    title = summary.run_tag or "评测报告"
    lines.append(f"# 评测报告：{title}")
    lines.append("")
    lines.append(
        f"- 开始 / 结束：{summary.started_at} → {summary.finished_at}"
        f"（耗时 {summary.duration_ms / 1000:.1f}s）"
    )
    lines.append(f"- 被测地址：`{summary.base_url}`")
    lines.append(f"- 对话模型：`{summary.model_name or '未标注'}`；场景过滤：`{summary.scene or '全部'}`")
    lines.append(
        f"- 用例集：`{summary.fixture_dir}`，共 {m.total} 条，"
        f"指纹 `{summary.fixture_hash}`，并发 {summary.concurrency}"
    )
    lines.append(
        f"- 链路回读：{'开启（RAG / token 按链路实测）' if summary.trace_readback else '关闭（RAG、token 指标不可用）'}"
    )
    if summary.baseline_at or summary.baseline_tag:
        lines.append(
            f"- 基线：{summary.baseline_tag or '上一次运行'}（{summary.baseline_at}，"
            f"指纹 `{summary.baseline_hash}`）"
        )
    lines.append("")

    regressed = [d for d in summary.diffs if d.regressed]
    lines.append("## 1. 结论")
    lines.append("")
    lines.append(
        f"- 通过率 **{_pct(m.pass_rate.value)}**（{m.passed}/{m.total}），"
        f"传输失败 {m.errors} 条"
    )
    lines.append(
        f"- 意图准确率 {_pct(m.intent_accuracy.value)}，工具命中率 {_pct(m.tool_hit_rate.value)}，"
        f"RAG 命中率 {_pct(m.rag_hit_rate.value)}"
    )
    if summary.diffs:
        if regressed:
            names = "、".join(f"{d.label}（{d.note}）" for d in regressed)
            lines.append(f"- **退化项 {len(regressed)} 个：{names}**。本次变更需要解释或回滚。")
        else:
            lines.append("- 与基线对比：**无指标退化**（容差内波动不判退化）。")
    else:
        lines.append("- 未提供基线，本次结果可作为后续回归的对照。")
    lines.append("")

    lines.append("## 2. 指标与基线对比")
    lines.append("")
    if summary.diffs:
        lines.append("| 指标 | 本次 | 基线 | 差值 | 判定 | 说明 |")
        lines.append("|---|---|---|---|---|---|")
        for d in summary.diffs:
            if d.regressed:
                verdict = "退化"
            elif not d.comparable:
                verdict = "不可比"
            elif d.delta is None:
                verdict = "持平"
            else:
                verdict = "正常"
            delta = "—" if d.delta is None else f"{d.delta:+.2f}"
            cur = _num(d.current) if d.direction == "down" else _pct(d.current)
            base = _num(d.baseline) if d.direction == "down" else _pct(d.baseline)
            lines.append(f"| {d.label} | {cur} | {base} | {delta} | {verdict} | {d.note} |")
    else:
        lines.append("_本次未指定基线，无对比数据。_")
    lines.append("")

    lines.append("## 3. 指标口径与样本数")
    lines.append("")
    lines.append("| 指标 | 值 | 分子/分母 | 不可观测 |")
    lines.append("|---|---|---|---|")
    for name, label, _, _ in METRIC_SPECS:
        point: MetricPoint = getattr(m, name)
        shown = _num(point.value) if name in ("p95_first_token_ms", "avg_total_ms", "avg_tokens") else _pct(point.value)
        lines.append(f"| {label} | {shown} | {_frac(point)} | {point.unobserved} |")
    lines.append("")
    lines.append(
        f"- 本轮实际消耗 token 合计 {m.total_tokens}（估算值，仅供纵横对比，后台不展示）；"
        "首字延迟由客户端计时，含网络往返，与后台链路的 `ttft_ms` 不同源。"
    )
    lines.append("")

    failures = summary.failures()
    lines.append(f"## 4. 失败用例（{len(failures)}）")
    lines.append("")
    if not failures:
        lines.append("_全部用例通过。_")
    for f in failures:
        lines.append(f"### {f.case_id} — {f.query}")
        lines.append("")
        lines.append(f"- 失败项：{f.reason}")
        if f.trace_id:
            lines.append(f"- trace_id：`{f.trace_id}`（后台链路页可按此检索）")
        if f.answer_excerpt:
            lines.append(f"- 应答摘录：{f.answer_excerpt}")
        lines.append("")
    lines.append("")

    lines.append("## 5. 全量用例")
    lines.append("")
    lines.append("| 用例 | 场景 | 结果 | 主意图 | 置信 | 工具 | RAG | 首字(ms) | token | 标签 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in summary.results:
        obs = r.observation
        rag = "—" if obs.rag_chunks is None else str(obs.rag_chunks)
        conf = "—" if obs.confidence is None else f"{obs.confidence:.2f}"
        lines.append(
            f"| {r.case_id} | {r.scene} | {'通过' if r.passed else '失败'} | "
            f"{obs.intents[0] if obs.intents else '—'} | {conf} | {len(obs.tools)} | {rag} | "
            f"{'—' if obs.first_token_ms is None else obs.first_token_ms} | "
            f"{'—' if obs.tokens is None else obs.tokens} | {'、'.join(r.tags)} |"
        )
    lines.append("")
    lines.append(
        "> 用例集进仓库、评审后才可改；改期望值来迁就结果会让这份报告失去意义。"
    )
    lines.append("")
    return "\n".join(lines)
