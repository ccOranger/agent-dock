"""管理后台：客户咨询信息管理（跨客户查看 + CSV 导出，只读）"""
import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.deps import require_staff
from app.core.exceptions import NotFound, ParamError
from app.models.repositories import MessageRepo, SceneRepo, SessionRepo
from app.schemas.common import ApiResponse, PaginatedData

router = APIRouter(prefix="/admin/inquiries", tags=["admin-inquiries"])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        from app.core.exceptions import ParamError

        raise ParamError(f"时间格式非法: {value}") from None


async def _build_filters(
    user_id: str | None,
    status: str | None,
    last_route_to: str | None,
    date_from: str | None,
    date_to: str | None,
    scene_code: str | None = None,
) -> dict:
    scene_id = None
    if scene_code:
        scene = await SceneRepo.get_by_code(scene_code)
        if scene is None:
            raise ParamError(f"场景不存在: {scene_code}")
        scene_id = str(scene.id)
    return {
        "user_id": user_id,
        "status": status,
        "last_route_to": last_route_to,
        "scene_id": scene_id,
        "date_from": _parse_dt(date_from),
        "date_to": _parse_dt(date_to),
    }


async def _scene_codes() -> dict[str, str]:
    return {str(s.id): s.code for s in await SceneRepo.list_all()}


@router.get("/sessions", response_model=ApiResponse[PaginatedData[dict]])
async def list_inquiries(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: str | None = None,
    status: str | None = None,
    last_route_to: str | None = None,
    scene_code: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    user=Depends(require_staff),
):
    """跨客户会话列表：支持按客户/状态/路由/场景/时间范围过滤。"""
    filters = await _build_filters(user_id, status, last_route_to, date_from, date_to, scene_code)
    items, total = await SessionRepo.list_all_filtered(page=page, page_size=page_size, **filters)
    scene_codes = await _scene_codes()
    for item in items:
        item["scene_code"] = scene_codes.get(item.get("scene_id"))
    return ApiResponse(
        data=PaginatedData(items=items, total=total, page=page, page_size=page_size)
    )


@router.get("/sessions/export")
async def export_inquiries(
    user_id: str | None = None,
    status: str | None = None,
    last_route_to: str | None = None,
    scene_code: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    user=Depends(require_staff),
):
    """导出满足过滤条件的会话为 CSV（UTF-8 BOM，Excel 友好）。"""
    filters = await _build_filters(user_id, status, last_route_to, date_from, date_to, scene_code)
    rows = await SessionRepo.export_rows(**filters)
    scene_codes = await _scene_codes()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["会话ID", "场景", "客户", "用户名", "标题", "状态", "最后路由", "创建时间", "首条问句"]
    )
    for r in rows:
        writer.writerow(
            [
                r["session_id"],
                scene_codes.get(r.get("scene_id"), ""),
                r["customer"],
                r["username"],
                r["title"],
                r["status"],
                r["last_route_to"],
                r["created_at"],
                r["excerpt"],
            ]
        )
    csv_bytes = buf.getvalue().encode("utf-8-sig")

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"inquiries_{stamp}.csv"
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sessions/{session_id}", response_model=ApiResponse[dict])
async def get_inquiry(session_id: str, user=Depends(require_staff)):
    """查看某会话的完整对话记录（staff 可访问任意会话）。"""
    sess = await SessionRepo.get(session_id)
    if sess is None:
        raise NotFound("会话不存在")
    scene = await SceneRepo.get(sess.scene_id)
    messages = await MessageRepo.list_by_session(session_id)
    return ApiResponse(
        data={
            "session": {
                "id": str(sess.id),
                "title": sess.title,
                "status": sess.status,
                "scene_id": str(sess.scene_id),
                "scene_code": scene.code if scene else None,
                "last_route_to": sess.last_route_to,
                "user_id": str(sess.user_id) if sess.user_id else None,
                "created_at": sess.created_at.isoformat(),
                "updated_at": sess.updated_at.isoformat(),
            },
            "messages": messages,
        }
    )
