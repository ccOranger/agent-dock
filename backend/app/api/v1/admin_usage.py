"""管理后台：用量报表与导出（账单口径，只读）

数据源只有 chat_usage_daily：明细 chat_events/request_traces 会按保留期清理，
日聚合长期保留，所以"对账"和"导出发票附件"都以它为准。
"""
import csv
import io
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.deps import require_perm
from app.core.config import settings
from app.core.exceptions import ParamError
from app.models.repositories import UsageRepo
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/admin/usage", tags=["admin-usage"])

# 维度取值：按天看趋势 / 按场景看哪个业务线在花额度 / 按接入方看该找谁结算
GROUP_BY_CHOICES = ("day", "scene", "integration")
DEFAULT_DAYS = 30


def _parse_day(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ParamError(f"{field} 日期格式非法（应为 YYYY-MM-DD）: {value}") from None


def _range(date_from: str | None, date_to: str | None) -> tuple[date, date]:
    """默认最近 30 天（含今天）；跨度上限由 USAGE_QUERY_MAX_DAYS 控制，防拖库。"""
    today = datetime.now().astimezone().date()
    end = _parse_day(date_to, "to") or today
    start = _parse_day(date_from, "from") or (end - timedelta(days=DEFAULT_DAYS - 1))
    if start > end:
        raise ParamError("起始日期不能晚于结束日期")
    span = (end - start).days + 1
    if span > settings.usage_query_max_days:
        raise ParamError(f"单次查询跨度不能超过 {settings.usage_query_max_days} 天")
    return start, end


def _check_group_by(group_by: str) -> str:
    if group_by not in GROUP_BY_CHOICES:
        raise ParamError(f"group_by 仅支持 {'/'.join(GROUP_BY_CHOICES)}")
    return group_by


@router.get("/summary", response_model=ApiResponse[dict])
async def usage_summary(
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    group_by: str = Query("day"),
    scene_code: str | None = None,
    user=Depends(require_perm("usage", "read")),
):
    """用量汇总：rows 按维度分组，totals 为同口径合计（二者必然自洽）。"""
    start, end = _range(date_from, date_to)
    group_by = _check_group_by(group_by)
    data = await UsageRepo.summary(
        group_by=group_by, date_from=start, date_to=end, scene_code=scene_code
    )
    return ApiResponse(
        data={
            "from": start.isoformat(),
            "to": end.isoformat(),
            "group_by": group_by,
            "scene_code": scene_code,
            "rows": data["rows"],
            "totals": data["totals"],
            # 标记当前 token 口径：hello_agents 不回传 usage，全部为估算值
            "tokens_estimated": True,
        }
    )


@router.get("/export.csv")
async def export_usage(
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    scene_code: str | None = None,
    user=Depends(require_perm("usage", "read")),
):
    """导出 (日 × 场景 × 接入方) 明细 CSV（UTF-8 BOM，Excel 直接打开无乱码）。"""
    start, end = _range(date_from, date_to)
    rows = await UsageRepo.export_rows(date_from=start, date_to=end, scene_code=scene_code)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["日期", "场景", "接入方", "接入方ID", "请求数", "Token（估算）", "其中估算请求", "转人工", "错误"]
    )
    for r in rows:
        writer.writerow(
            [
                r["day"],
                r["scene_code"],
                r["integration_name"] or "未归属",
                r["integration_id"],
                r["requests"],
                r["tokens"],
                r["estimated_requests"],
                r["handoffs"],
                r["errors"],
            ]
        )
    csv_bytes = buf.getvalue().encode("utf-8-sig")

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"usage_{stamp}.csv"
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
