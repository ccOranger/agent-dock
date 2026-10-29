"""管理后台：数据分析（成效指标 + 每日请求量，只读）

两组口径刻意分开呈现，避免把"采样值"当"全量事实"：

- **全量三率 + 每日请求量**（转人工 / 问题解决 / 错误 + 按天 requests/handoffs/errors）：
  来自 ``chat_usage_daily``，全量且长期保留，无采样偏差；
- **链路三率**（工具调用成功 / 兜底降级 / RAG 命中）：来自 ``request_traces``，
  受 ``TRACE_SAMPLE_RATE`` 采样影响，分母是"走完链路并落库的请求"；
  采样率 < 1 时工具成功率会被低估，兜底率与 RAG 命中率是比值参考。

**token 不对内展示**：``hello_agents`` 不回传供应商 usage，只能估算，金额/账单不应基于它；
库内 `chat_events.total_tokens` / `chat_usage_daily.tokens` 仍照常记录（以后接入真实 usage 可再用），
但接口与页面不再输出。

"幻觉率"本期不做：没有 ground-truth，后续走离线评测（LLM-as-judge）另行提供。
"""
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_perm
from app.core.config import settings
from app.core.exceptions import ParamError
from app.models.repositories import TraceRepo, UsageRepo
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/admin/analytics", tags=["admin-analytics"])

DEFAULT_DAYS = 7


def _parse_day(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ParamError(f"{field} 日期格式非法（应为 YYYY-MM-DD）: {value}") from None


def _range(date_from: str | None, date_to: str | None) -> tuple[date, date]:
    """缺省取最近 7 天（含今天）；跨度上限沿用 USAGE_QUERY_MAX_DAYS，防拖库。"""
    today = datetime.now().astimezone().date()
    end = _parse_day(date_to, "to") or today
    start = _parse_day(date_from, "from") or (end - timedelta(days=DEFAULT_DAYS - 1))
    if start > end:
        raise ParamError("起始日期不能晚于结束日期")
    span = (end - start).days + 1
    if span > settings.usage_query_max_days:
        raise ParamError(f"单次查询跨度不能超过 {settings.usage_query_max_days} 天")
    return start, end


def _local_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """本地日 → aware datetime 边界。

    日聚合按进程本地日期记账，链路按 timestamptz 落库；这里统一用本地日边界过滤，
    保证"全量三率"与"链路三率"落在同一个时间窗内（用 UTC 边界会让国内部署偏移 8 小时）。
    """
    tz = datetime.now().astimezone().tzinfo
    return (
        datetime.combine(start, time.min, tzinfo=tz),
        datetime.combine(end, time.max, tzinfo=tz),
    )


def _rate(numerator: int, denominator: int) -> dict:
    """比率 + 分子分母：空数据返回 value=None（前端显示"—"），不报错也不假装 0%。"""
    return {
        "value": round(numerator / denominator, 4) if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


@router.get("/outcomes", response_model=ApiResponse[dict])
async def analytics_outcomes(
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    scene_code: str | None = None,
    user=Depends(require_perm("analytics", "read")),
):
    """成效指标 + 每日请求量（默认近 7 天，可按场景过滤）。"""
    start, end = _range(date_from, date_to)

    # 全量口径：一次 day 维度汇总，totals 算三率、rows 直接做每日统计
    usage = await UsageRepo.summary(
        group_by="day", date_from=start, date_to=end, scene_code=scene_code
    )
    totals = usage["totals"]
    requests = int(totals["requests"])
    handoffs = int(totals["handoffs"])
    errors = int(totals["errors"])
    # 转人工/错误的轮次不算"机器人自主应答"；防御性取 max(0)，避免脏数据出现负值
    resolved = max(requests - handoffs - errors, 0)
    # 每日统计只暴露请求/转人工/错误：token 是估算值，不对外展示
    daily = [
        {
            "day": row["day"],
            "requests": int(row["requests"]),
            "handoffs": int(row["handoffs"]),
            "errors": int(row["errors"]),
        }
        for row in usage["rows"]
    ]

    # 链路三率：一次聚合把全部计数据回来
    trace_from, trace_to = _local_bounds(start, end)
    trace = await TraceRepo.outcome_summary(
        date_from=trace_from, date_to=trace_to, scene_code=scene_code
    )
    tool_calls = int(trace["tool_calls"])
    tool_errors = int(trace["tool_errors"])
    traces = int(trace["traces"])

    sample_rate = float(settings.trace_sample_rate)

    return ApiResponse(
        data={
            "from": start.isoformat(),
            "to": end.isoformat(),
            "scene_code": scene_code,
            "handover_rate": _rate(handoffs, requests),
            "resolution_rate": _rate(resolved, requests),
            "error_rate": _rate(errors, requests),
            "tool_success_rate": _rate(max(tool_calls - tool_errors, 0), tool_calls),
            "fallback_rate": _rate(int(trace["fallbacks"]), traces),
            "rag_hit_rate": _rate(int(trace["rag_hits"]), traces),
            "daily": daily,
            "meta": {
                "full_source": "chat_usage_daily",
                "trace_source": "request_traces",
                "trace_sample_rate": sample_rate,
                "sampled": sample_rate < 1,
                "requests": requests,
                "traces_covered": traces,
            },
        }
    )
