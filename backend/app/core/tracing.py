"""请求链路采集：把一轮对话经过的 Router / Agent / MCP / 工具 / RAG / 记忆 收拢成 span 时间线

设计要点：
- 纯内存累加，不做任何 IO；落库由调用方（ChatService）在流结束后一次性完成
- 维度拍平成标量/数组列便于后台过滤，明细统一放 spans JSONB
- 工具入参与返回属客户数据：受 TRACE_STORE_PAYLOAD / TRACE_PAYLOAD_MAX_CHARS 约束
- 用量（prompt/completion token）与链路开关、采样**解耦**：链路可以不记，账必须有
"""
from __future__ import annotations

import json
import random
import time
import uuid

from app.core.config import settings
from app.utils.tokens import estimate_tokens

# ===== span 类型（后台按类型分组着色）=====
SPAN_ROUTER = "router.llm"
# 场景直连：没有 LLM 调用，但仍记一条 span，后台才能解释“这轮为什么没走路由”
SPAN_ROUTE = "route.decision"
SPAN_INTENT = "route.intent"
SPAN_MEMORY = "context.memory"
SPAN_RAG = "context.rag"
SPAN_RESOLVE = "agent.resolve"
SPAN_BUILD = "agent.build"
SPAN_RUN = "agent.run"
SPAN_TOOL = "tool.call"
SPAN_PERSIST = "memory.persist"
# 高频固定话术命中（身份/能力/探听配置）：不经路由与 Agent，单独留一条 span 便于后台核查话术命中情况
SPAN_CANNED = "canned.script"

# ===== 请求结局 =====
OUTCOME_ANSWERED = "answered"
OUTCOME_HANDOVER = "handover"
OUTCOME_ERROR = "error"


def _as_text(value) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def clip_payload(value, limit: int | None = None) -> tuple[str, bool]:
    """客户数据入库前的裁剪：关闭开关则只留类型摘要，避免链路表变成隐私仓库。"""
    if not settings.trace_store_payload:
        text = _as_text(value)
        return (f"<payload omitted:{len(text)}chars>", True) if text else ("", False)
    text = _as_text(value)
    max_len = limit or settings.trace_payload_max_chars
    if len(text) > max_len:
        return text[:max_len], True
    return text, False


class TraceCollector:
    """一次请求（一轮对话）的链路累加器。

    所有方法在 disabled 时直接返回，调用方无需自行判断开关。
    """

    def __init__(
        self,
        trace_id: str,
        user_id: str | None = None,
        session_id: str | None = None,
        scene_code: str | None = None,
        visitor_id: str | None = None,
        enabled: bool = True,
    ):
        self.trace_id = trace_id
        self.user_id = user_id
        self.session_id = session_id
        self.scene_code = scene_code
        self.visitor_id = visitor_id
        self.enabled = bool(enabled and settings.trace_enabled)

        self._t0 = time.monotonic()
        self._open: dict[str, dict] = {}
        self.spans: list[dict] = []

        # ---- 拍平的可过滤维度 ----
        self.agent_names: list[str] = []
        self.agent_sources: list[str] = []
        self.llm_models: list[str] = []
        self.mcp_server_names: list[str] = []
        self.tool_names: list[str] = []
        self.rag_namespaces: list[str] = []

        # ---- 计数与结论 ----
        self.intent_count = 0
        self.confidence: float | None = None
        # 路由旁路标记：直连/单 Agent 场景跳过路由 LLM 时置位，M3 起写入 router_ms/prompt_hash
        self.router_skipped = False
        self.router_fallback = False
        self.router_ms: int | None = None
        self.prompt_hash: str | None = None
        self.memory_hit_count = 0
        self.rag_chunk_count = 0
        self.tool_call_count = 0
        self.tool_error_count = 0
        self.response_chars = 0
        # ---- 用量计量（不受 enabled / 采样影响，见 record_prompt）----
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.outcome: str | None = None
        self.handover_reason: str | None = None
        self.error_code: int | None = None
        self.error_message: str | None = None
        self.ttft_ms: int | None = None
        self.total_latency_ms: int | None = None
        self.user_message_id: str | None = None
        self.assistant_message_id: str | None = None

    # ------------------------------------------------------------------ 时间
    def now_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def mark_first_token(self) -> None:
        if self.enabled and self.ttft_ms is None:
            self.ttft_ms = self.now_ms()

    def mark_finished(self) -> None:
        if self.enabled and self.total_latency_ms is None:
            self.total_latency_ms = self.now_ms()

    # ------------------------------------------------------------------ span
    def add(
        self,
        type_: str,
        name: str,
        *,
        parent: str | None = None,
        started_ms: int | None = None,
        duration_ms: int | None = None,
        status: str = "ok",
        **attrs,
    ) -> str:
        """直接落一条已完成 span，返回 span_id（未启用时返回空串）。"""
        if not self.enabled:
            return ""
        end_ms = self.now_ms()
        start = end_ms if started_ms is None else started_ms
        span = {
            "span_id": uuid.uuid4().hex[:12],
            "parent_id": parent,
            "type": type_,
            "name": name,
            "start_ms": start,
            "duration_ms": (end_ms - start) if duration_ms is None else duration_ms,
            "status": status,
            "attributes": {k: v for k, v in attrs.items() if v is not None},
        }
        self.spans.append(span)
        return span["span_id"]

    def start(self, type_: str, name: str, *, parent: str | None = None, **attrs) -> str:
        """开始一条跨事件相关的 span（工具调用：call 与 result 分两次到达）。"""
        if not self.enabled:
            return ""
        key = uuid.uuid4().hex[:12]
        self._open[key] = {
            "span_id": key,
            "parent_id": parent,
            "type": type_,
            "name": name,
            "start_ms": self.now_ms(),
            "attributes": {k: v for k, v in attrs.items() if v is not None},
        }
        return key

    def end(self, key: str, *, status: str = "ok", **attrs) -> None:
        if not self.enabled or not key:
            return
        open_span = self._open.pop(key, None)
        if open_span is None:
            return
        merged = {**open_span["attributes"], **{k: v for k, v in attrs.items() if v is not None}}
        self.spans.append(
            {
                "span_id": key,
                "parent_id": open_span["parent_id"],
                "type": open_span["type"],
                "name": open_span["name"],
                "start_ms": open_span["start_ms"],
                "duration_ms": self.now_ms() - open_span["start_ms"],
                "status": status,
                "attributes": merged,
            }
        )

    # ------------------------------------------------------------------ 用量
    def record_prompt(self, text: str | None) -> None:
        """记录一段真正送进模型的提示词（多意图/路由各算一次）。

        刻意不判断 self.enabled：TRACE_ENABLED=false 或采样丢弃时链路没了，
        用量仍要落到 chat_usage_daily，否则报表会漏计。
        """
        self.prompt_tokens += estimate_tokens(text)

    def record_completion(self, text: str | None) -> None:
        """记录模型最终下发的正文（同样不受链路开关影响）。"""
        self.completion_tokens += estimate_tokens(text)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    # -------------------------------------------------------------- 业务语义
    def add_unique(self, bucket: str, value: str | None) -> None:
        """往拍平维度里追加去重值（bucket 为 agent_names / tool_names 等列表属性）。"""
        if not self.enabled or not value:
            return
        target: list[str] = getattr(self, bucket)
        if value not in target:
            target.append(value)

    def record_intents(self, intents: list[dict], max_intents: int) -> None:
        """路由结果：意图数量、主意图置信度与每个意图的分类依据。"""
        if not self.enabled:
            return
        self.intent_count = len(intents)
        if intents:
            self.confidence = float(intents[0].get("confidence") or 0.0)
        for idx, intent in enumerate(intents[:max_intents]):
            self.add(
                SPAN_INTENT,
                intent.get("agent_name", ""),
                status="ok",
                intent_index=idx,
                confidence=intent.get("confidence"),
                reason=_clip(intent.get("reason"))[0],
                # 兜底意图的置信度是约定值，打标后后台才能区分“兜底应答”与“模型真低置信”
                is_fallback=intent.get("is_fallback") or None,
                # 意图依据：这句话命中的路由关键词（本地子串匹配，参考）+ 命中状况
                matched_keywords=list(intent.get("matched_keywords") or []) or None,
                clue_basis=intent.get("clue_basis"),
            )

    def record_tool_call(self, name: str, args, agent_name: str | None = None, iteration: int | None = None) -> str:
        if not self.enabled:
            return ""
        self.tool_call_count += 1
        self.add_unique("tool_names", name)
        args_text, args_truncated = clip_payload(args)
        return self.start(
            SPAN_TOOL,
            name,
            agent_name=agent_name,
            iteration=iteration,
            args=args_text or None,
            args_truncated=args_truncated or None,
        )

    def finish_tool_call(self, key: str, result=None, error: str | None = None) -> None:
        if not self.enabled or not key:
            return
        if error:
            self.tool_error_count += 1
        result_text, result_truncated = clip_payload(result)
        self.end(
            key,
            status="error" if error else "ok",
            result=result_text or None,
            result_truncated=result_truncated or None,
            error=error,
        )

    def record_outcome(
        self,
        outcome: str,
        *,
        reason: str | None = None,
        code: int | None = None,
        message: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.outcome = outcome
        if outcome == OUTCOME_HANDOVER:
            self.handover_reason = reason
        if outcome == OUTCOME_ERROR:
            self.error_code = code
            self.error_message = _clip(message)[0] or None

    # ------------------------------------------------------------------ 落库
    def should_persist(self) -> bool:
        """异常/转人工永远全量保留；正常应答按采样率丢弃。"""
        if not self.enabled:
            return False
        if self.outcome in (OUTCOME_HANDOVER, OUTCOME_ERROR) or self.tool_error_count:
            return True
        rate = float(settings.trace_sample_rate)
        if rate >= 1:
            return True
        if rate <= 0:
            return False
        return random.random() < rate

    def to_row(self) -> dict:
        """拍平成 request_traces 表的一行（不含 spans 之外的 ORM 默认值）。"""
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "scene_code": self.scene_code or "default",
            "visitor_id": self.visitor_id,
            "user_message_id": self.user_message_id,
            "assistant_message_id": self.assistant_message_id,
            "intent_count": self.intent_count,
            "router_skipped": self.router_skipped,
            "router_fallback": self.router_fallback,
            "router_ms": self.router_ms,
            "prompt_hash": self.prompt_hash,
            "agent_names": list(self.agent_names),
            "agent_sources": list(self.agent_sources),
            "llm_models": list(self.llm_models),
            "mcp_server_names": list(self.mcp_server_names),
            "tool_names": list(self.tool_names),
            "rag_namespaces": list(self.rag_namespaces),
            "memory_hit_count": self.memory_hit_count,
            "rag_chunk_count": self.rag_chunk_count,
            "tool_call_count": self.tool_call_count,
            "tool_error_count": self.tool_error_count,
            "confidence": self.confidence,
            "outcome": self.outcome,
            "handover_reason": self.handover_reason,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "ttft_ms": self.ttft_ms,
            "total_latency_ms": self.total_latency_ms,
            "response_chars": self.response_chars,
            "spans": list(self.spans),
        }


def _clip(value, limit: int | None = None) -> tuple[str, bool]:
    """内部快捷：允许 None 直接透传。"""
    if value is None:
        return "", False
    return clip_payload(value, limit)


def new_request_trace_id() -> str:
    """与 structlog 的 trace_id 对齐的请求标识（16 位十六进制，可直接 grep 日志）。"""
    return uuid.uuid4().hex[:16]
