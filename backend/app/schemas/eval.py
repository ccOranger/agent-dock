"""评测与回归的数据模型（用例集 / 实测观测 / 结果 / 指标）

口径说明（详见 docs/evaluation.md）：
- 一条用例 = 一个会话内的若干轮"铺垫 + 正式提问"，只判定最后一轮；
- 期望项全部可选：没写的检查点不产生判定，也就不会计入指标分母；
- 观测不到 ≠ 不达标：链路被采样丢弃时 RAG/token 无从回读，这类用例计入"不可观测"
  而不是计入失败，否则报表会把采样问题误报成效果退化。
"""
from pydantic import BaseModel, Field, model_validator

# 用例来源：有真实客户数据后按 60/40 替换为线上抽样，来源字段就是替换进度表
SOURCE_DEMO_CORPUS = "demo_corpus"   # 由仓库演示知识库（docs/demo/*.md）派生的正常问法
SOURCE_MANUAL = "manual"             # 人工构造的边界用例（低置信、超纲、多意图、追问）
SOURCE_TRACE_SAMPLE = "trace_sample"  # 真实 request_traces 抽样（客户现场积累后回填）
SOURCES = (SOURCE_DEMO_CORPUS, SOURCE_MANUAL, SOURCE_TRACE_SAMPLE)


class EvalExpect(BaseModel):
    """期望项：只写本次要校验的维度，缺省即不校验。"""

    # 主意图期望承接的 Agent；与 agents_any 同时给出时以 agent 为准、agents_any 放宽
    agent: str | None = None
    agents_any: list[str] = Field(default_factory=list)
    # 多意图：至少识别出这么多意图，且这些 Agent 都要出现在意图清单里
    min_intents: int | None = Field(default=None, ge=1)
    intents_include: list[str] = Field(default_factory=list)
    # 工具：tools_all 必须都被调用，tools_any 至少命中一个
    tools_all: list[str] = Field(default_factory=list)
    tools_any: list[str] = Field(default_factory=list)
    # RAG：expect_rag=True 要求检索到片段（可选校验 namespace）；
    # rag_empty=True 用于超纲问题——期望知识库确实帮不上忙
    expect_rag: bool | None = None
    rag_namespace: str | None = None
    rag_empty: bool | None = None
    # 转人工 / 兜底：期望本轮是否走到这两个结局
    handover: bool | None = None
    fallback: bool | None = None
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    # 应答文本：包含式校验（keywords_any 任一、keywords_all 全部）与禁词
    keywords_any: list[str] = Field(default_factory=list)
    keywords_all: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)
    max_answer_chars: int | None = Field(default=None, ge=1)
    # 工具上限：政策边界用例用—— default 场景不该替客户改单，调了工具就是越权
    max_tools: int | None = Field(default=None, ge=0)
    # 期望本轮在入口就被拦下（注入检测/鉴权/限流），不进模型：
    # true=必须被拒，false=必须跑通
    rejected: bool | None = None


class EvalCase(BaseModel):
    """一条评测用例（用例集文件里 cases[] 的一项）。"""

    case_id: str = Field(min_length=1, max_length=64)
    # 留空表示“与用例集文件同场景”（绝大多数情况）；显式写另一个场景就是跨场景回归用例
    scene: str = Field(default="", max_length=32)
    query: str = Field(min_length=1, max_length=4000)
    # 正式提问前的铺垫轮次：同会话内依次发出，用于追问续接、跨轮记忆类用例
    setup: list[str] = Field(default_factory=list)
    expect: EvalExpect = Field(default_factory=EvalExpect)
    tags: list[str] = Field(default_factory=list)
    source: str = SOURCE_MANUAL
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _check_source(self) -> "EvalCase":
        if self.source not in SOURCES:
            raise ValueError(f"case {self.case_id}: source 必须是 {SOURCES} 之一")
        return self


class EvalCaseFile(BaseModel):
    """一个用例集文件：<fixture_dir>/<scene_code>.json"""

    scene: str = Field(min_length=1, max_length=32)
    description: str = ""
    cases: list[EvalCase]

    @model_validator(mode="after")
    def _check_scene_match(self) -> "EvalCaseFile":
        # 用例可以指向别的场景（跨场景回归），但写了就只能是真实场景名，
        # 且多数用例与文件名不一致时一定是写错了
        explicit = [c for c in self.cases if c.scene and c.scene != self.scene]
        if len(explicit) * 2 > len(self.cases):
            raise ValueError(
                f"{self.scene}.json 中多数用例的 scene 与文件名不一致："
                f"{[c.case_id for c in explicit][:5]}"
            )
        return self


class EvalObservation(BaseModel):
    """一轮对话的实测事实（由执行器采集，打分只看它，不碰 HTTP/DB）"""

    case_id: str
    # 传输层是否跑通：False 时所有检查点都判失败，并在报告里单列原因
    executed: bool = True
    exec_error: str = ""
    # 未跑通时的具体状态：HTTP 状态码与 SSE error 事件的业务码（拒答类用例靠它判定）
    http_status: int | None = None
    error_code: int | None = None
    trace_id: str = ""
    session_id: str = ""
    # 路由：intents 为 route 事件里的意图清单（按顺序）
    intents: list[str] = Field(default_factory=list)
    confidence: float | None = None
    fallback: bool = False
    handover: bool = False
    handover_reason: str = ""
    # 工具：SSE tool_call 事件里出现过的工具名（去重保序）
    tools: list[str] = Field(default_factory=list)
    tool_error_count: int = 0
    # RAG 与 token 来自链路/埋点回读：None 表示本轮不可观测（采样丢弃或未落库）
    rag_chunks: int | None = None
    rag_namespaces: list[str] = Field(default_factory=list)
    tokens: int | None = None
    # 客户端计时：首字延迟含网络往返，与后台 ttft_ms 口径不同，报告里注明
    first_token_ms: int | None = None
    total_ms: int = 0
    answer: str = ""
    answer_chars: int = 0


class EvalCheck(BaseModel):
    """单个检查点的判定结果"""

    name: str
    ok: bool
    detail: str = ""


class CaseResult(BaseModel):
    """一条用例的最终结果

    checks 只装"产生了判定"的检查点；因观测不到而跳过的记在 skipped 里
    （比如链路被采样丢弃时 RAG 片段数无从回读）——两者都不能算失败，
    也都不进对应指标的分母，否则采样问题会被误报成效果退化。
    """

    case_id: str
    scene: str
    query: str
    tags: list[str] = Field(default_factory=list)
    source: str = SOURCE_MANUAL
    passed: bool
    checks: list[EvalCheck] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    # 用例写了哪些维度的期望（与是否产生判定无关）：指标分母与"不可观测"靠它区分
    # "这条用例本来就不关心 RAG" 与 "关心但链路没观测到"，两者都不能进分母
    expected: list[str] = Field(default_factory=list)
    observation: EvalObservation
    answer_excerpt: str = ""


class MetricPoint(BaseModel):
    """单项指标：rate 类带分子分母，数值类带样本数"""

    value: float | None = None
    numerator: int = 0
    denominator: int = 0
    # 不可观测（采样丢弃/未落库）的样本数，与分母分开列出
    unobserved: int = 0


class EvalMetrics(BaseModel):
    """一次运行的指标汇总"""

    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0          # 传输层就没跑通的用例
    pass_rate: MetricPoint = Field(default_factory=MetricPoint)
    intent_accuracy: MetricPoint = Field(default_factory=MetricPoint)
    tool_hit_rate: MetricPoint = Field(default_factory=MetricPoint)
    rag_hit_rate: MetricPoint = Field(default_factory=MetricPoint)
    fallback_rate: MetricPoint = Field(default_factory=MetricPoint)
    handover_rate: MetricPoint = Field(default_factory=MetricPoint)
    p95_first_token_ms: MetricPoint = Field(default_factory=MetricPoint)
    avg_total_ms: MetricPoint = Field(default_factory=MetricPoint)
    avg_tokens: MetricPoint = Field(default_factory=MetricPoint)
    total_tokens: int = 0

    def as_dict(self) -> dict:
        return self.model_dump()


class CaseFailure(BaseModel):
    """报告与 eval_runs.detail 里的失败明细（应答截断，不存全文）"""

    case_id: str
    query: str
    reason: str
    answer_excerpt: str = ""
    trace_id: str = ""


class MetricDiff(BaseModel):
    """与基线的单项对比：direction 标明这个指标是越大越好还是越小越好"""

    name: str
    label: str
    current: float | None = None
    baseline: float | None = None
    delta: float | None = None
    direction: str = "up"      # up=越大越好，down=越小越好
    regressed: bool = False
    comparable: bool = True     # 用例集指纹不同时不可比
    note: str = ""


class EvalRunSummary(BaseModel):
    """一次运行的完整快照：报告、JSON sidecar 与 eval_runs 行都由它渲染"""

    run_tag: str = ""
    scene: str = ""              # 本次过滤的场景，空=全部
    base_url: str = ""
    model_name: str = ""
    fixture_dir: str = ""
    fixture_hash: str = ""
    concurrency: int = 1
    trace_readback: bool = True  # 是否回读了链路（关掉时 RAG 指标不可观测）
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    metrics: EvalMetrics = Field(default_factory=EvalMetrics)
    results: list[CaseResult] = Field(default_factory=list)
    diffs: list[MetricDiff] = Field(default_factory=list)
    baseline_hash: str = ""
    baseline_tag: str = ""
    baseline_at: str = ""

    def failures(self) -> list[CaseFailure]:
        out: list[CaseFailure] = []
        for item in self.results:
            if item.passed:
                continue
            reason = item.observation.exec_error or "、".join(item.failed_checks) or "未通过"
            out.append(
                CaseFailure(
                    case_id=item.case_id,
                    query=item.query,
                    reason=reason,
                    answer_excerpt=item.answer_excerpt,
                    trace_id=item.observation.trace_id,
                )
            )
        return out

    def as_detail_rows(self) -> list[dict]:
        """写进 eval_runs.detail 的逐用例明细（只留结论与截断应答）"""
        rows: list[dict] = []
        for item in self.results:
            obs = item.observation
            rows.append(
                {
                    "case_id": item.case_id,
                    "scene": item.scene,
                    "passed": item.passed,
                    "failed": item.failed_checks,
                    "skipped": item.skipped,
                    "intents": obs.intents,
                    "tools": obs.tools,
                    "handover": obs.handover,
                    "fallback": obs.fallback,
                    "confidence": obs.confidence,
                    "rag_chunks": obs.rag_chunks,
                    "tokens": obs.tokens,
                    "first_token_ms": obs.first_token_ms,
                    "total_ms": obs.total_ms,
                    "trace_id": obs.trace_id,
                    "answer_excerpt": item.answer_excerpt,
                }
            )
        return rows
