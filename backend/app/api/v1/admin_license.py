"""管理后台：授权与版本信息（只读 + 重新加载）

只暴露"当前是什么版本、能用哪些功能位、还有多久到期"，不提供任何签发能力：
签发脚本与私钥在私有仓库，开源仓库只有校验与挂载点（决策 D6）。
"""
import logging

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.core.license import (
    COMMUNITY_SCOPE,
    EDITION_ENTERPRISE,
    EXPIRING_SOON_DAYS,
    FEATURE_CATALOG,
    LicenseStatus,
    license_status,
    reload_license,
)
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/license", tags=["admin-license"])


def _payload(status: LicenseStatus) -> dict:
    expiring = (
        status.valid
        and status.days_left is not None
        and status.days_left <= EXPIRING_SOON_DAYS
    )
    return {
        **status.as_dict(),
        "edition_label": "企业版" if status.edition == EDITION_ENTERPRISE else "社区版",
        # 功能位目录由后端下发：前端不再维护一份文案，新增功能位只改一处
        "feature_catalog": FEATURE_CATALOG,
        "community_scope": list(COMMUNITY_SCOPE),
        "expiring_soon": expiring,
        "expiring_soon_days": EXPIRING_SOON_DAYS,
    }


@router.get("", response_model=ApiResponse[dict])
async def get_license(user=Depends(require_admin)):
    """当前授权状态：社区/企业版、功能位、到期天数、降级原因。"""
    return ApiResponse(data=_payload(license_status()))


@router.post("/reload", response_model=ApiResponse[dict])
async def reload_license_file(user=Depends(require_admin)):
    """换授权文件后无需重启：重新读取并校验，返回新状态（失败同样只降级）。"""
    status = reload_license()
    logger.info(
        "授权已重新加载: edition=%s valid=%s reason=%s", status.edition, status.valid, status.reason
    )
    return ApiResponse(data=_payload(status))
