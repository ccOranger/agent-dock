"""License 校验：离线 Ed25519 签名 + 功能位（只降级，不阻断）

设计约束（docs/productization-plan.md 3.3，决策 D2 / D5 / D6）：

- **校验完全离线**：授权文件是「JSON 负载 + signature」，公钥由 `LICENSE_PUBLIC_KEY`
  提供（Base64 裸 32 字节或 PEM）。私钥与签发脚本只在私有仓库，开源仓库不内置任何
  可用私钥——否则删一行 `has_feature()` 之外，任何人也能自己签一份企业授权。
- **只降级不阻断**：文件缺失 / 解析失败 / 签名不符 / 过期，一律退回 community，
  对话与社区版功能照常可用。付费客户生产停摆的代价远大于防破解收益。
- **指纹不用机器码**：容器 hostname 每次重启都会变，改用 `.env` 的 `DEPLOYMENT_ID`
  （初始化时生成一次，随数据卷长期存在），避免“重启即掉授权”。留空表示未绑定，
  跳过指纹校验（开发/演示便利），生产部署应在 `.env` 里固定。
- **本模块只提供挂载点**：企业功能实现不在开源仓库，`has_feature()` 只是开关。
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.core.config import settings

logger = logging.getLogger(__name__)

EDITION_COMMUNITY = "community"
EDITION_ENTERPRISE = "enterprise"
EDITIONS = (EDITION_COMMUNITY, EDITION_ENTERPRISE)

LICENSE_VERSION = 1
# 授权到期前多少天开始在日志与后台提示续费
EXPIRING_SOON_DAYS = 30

# 功能位目录：只此 5 项属企业版，且都不动对话内核。
# label/desc 供后台授权页直接渲染，避免前端再维护一份文案。
FEATURE_CATALOG: dict[str, dict[str, str]] = {
    "rbac": {
        "label": "精细化权限",
        "desc": "自定义角色与权限矩阵（细分到场景/知识库/工单的读写）",
    },
    "audit": {
        "label": "审计日志",
        "desc": "后台操作审计的查询、筛选与 CSV 导出",
    },
    "white_label": {
        "label": "白标定制",
        "desc": "品牌名、Logo、主题色、登录页与嵌入挂件品牌",
    },
    "eval_pro": {
        "label": "评测管理",
        "desc": "评测集管理、定时回归与对比看板（基础评测与报告不在此位）",
    },
    "sso": {
        "label": "单点登录",
        "desc": "OIDC / 企业微信 / 钉钉登录",
    },
}
KNOWN_FEATURES = tuple(FEATURE_CATALOG)

# 社区版明确保留的能力（不设功能位，仅用于授权页展示，避免客户误以为都要买）
COMMUNITY_SCOPE = (
    "完整对话与多意图路由",
    "多知识库 RAG 检索",
    "工具 / MCP 接入",
    "跨轮记忆与记忆晋升",
    "全链路追踪与用量报表",
    "场景管理、嵌入挂件、基础评测 CLI 与 Markdown 报告",
)


@dataclass(frozen=True)
class LicenseStatus:
    """一次校验的结论。字段全部可直接返回给后台授权页。"""

    edition: str = EDITION_COMMUNITY
    customer: str = ""
    # features = 授权文件声明的功能位（过期后仍保留，便于后台展示“曾授权”）
    features: frozenset[str] = field(default_factory=frozenset)
    # active_features = 当前真正生效的功能位（校验失败 / 过期时为空）
    active_features: frozenset[str] = field(default_factory=frozenset)
    issued_at: str | None = None
    expires_at: str | None = None
    contract_ref: str | None = None
    deployment_id: str | None = None
    mode: str = "on_prem"
    source: str | None = None
    # 授权文件是否存在：社区版开箱即用不该报错，只有“放了文件却不生效”才需要横幅提醒
    configured: bool = False
    valid: bool = False
    expired: bool = False
    days_left: int | None = None
    # 本机 DEPLOYMENT_ID 是否与授权一致；本机未配置时为 False（跳过校验）
    deployment_bound: bool = False
    unknown_features: tuple[str, ...] = ()
    reason: str = "未配置授权文件（社区版）"

    def as_dict(self) -> dict:
        return {
            "edition": self.edition,
            "customer": self.customer,
            "features": sorted(self.features),
            "active_features": sorted(self.active_features),
            "unknown_features": list(self.unknown_features),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "contract_ref": self.contract_ref,
            "deployment_id": self.deployment_id,
            "deployment_bound": self.deployment_bound,
            "mode": self.mode,
            "source": self.source,
            "configured": self.configured,
            "valid": self.valid,
            "expired": self.expired,
            "days_left": self.days_left,
            "reason": self.reason,
        }


def canonical_payload(payload: dict) -> bytes:
    """签名口径：去掉 signature 后按键排序、无空格的紧凑 JSON（UTF-8）。

    签发端必须用同一口径（见 docs/configuration.md 的授权文件示例），
    否则会出现“文件看着没问题但签名不符”。
    """
    body = {k: v for k, v in payload.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _decode_b64(value: str) -> bytes:
    raw = (value or "").strip()
    return base64.b64decode(raw + "=" * (-len(raw) % 4), validate=False)


def parse_public_key(raw: str) -> Ed25519PublicKey | None:
    """支持 PEM 或 Base64 裸 32 字节；空值/格式错误返回 None（调用方降级）。"""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if "BEGIN PUBLIC KEY" in text:
            from cryptography.hazmat.primitives import serialization

            key = serialization.load_pem_public_key(text.encode("utf-8"))
            return key if isinstance(key, Ed25519PublicKey) else None
        data = _decode_b64(text)
        return Ed25519PublicKey.from_public_bytes(data) if len(data) == 32 else None
    except (ValueError, TypeError):
        return None


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def verify_document(
    document: dict,
    *,
    public_key: Ed25519PublicKey | None,
    deployment_id: str = "",
    now: datetime | None = None,
    source: str | None = None,
) -> LicenseStatus:
    """纯函数校验（不读文件、不碰配置），便于单测覆盖各种降级分支。"""
    moment = now or datetime.now(UTC)
    signature = document.get("signature")
    if not isinstance(signature, str) or not signature:
        return LicenseStatus(source=source, configured=True, reason="授权文件缺少 signature")
    if public_key is None:
        return LicenseStatus(
            source=source,
            configured=True,
            reason="未配置 LICENSE_PUBLIC_KEY（无法校验签名，降级为社区版）",
        )
    try:
        public_key.verify(_decode_b64(signature), canonical_payload(document))
    except (InvalidSignature, ValueError):
        return LicenseStatus(
            source=source,
            configured=True,
            reason="签名校验失败：文件被修改或不是本产品签发的授权",
        )

    if document.get("v") != LICENSE_VERSION:
        return LicenseStatus(
            source=source,
            configured=True,
            reason=f"不支持的授权文件版本 v={document.get('v')!r}（期望 {LICENSE_VERSION}）",
        )

    edition = str(document.get("edition") or "").strip().lower()
    if edition not in EDITIONS:
        return LicenseStatus(
            source=source, configured=True, reason=f"未知版本类型 edition={edition!r}"
        )

    declared = [str(f).strip().lower() for f in (document.get("features") or []) if str(f).strip()]
    features = frozenset(f for f in declared if f in FEATURE_CATALOG)
    unknown = tuple(sorted({f for f in declared if f not in FEATURE_CATALOG}))

    expires = _parse_time(document.get("expires_at"))
    days_left = None if expires is None else (expires - moment).days
    expired = expires is not None and expires <= moment

    licensed_deployment = str(document.get("deployment_id") or "").strip()
    local_deployment = (deployment_id or "").strip()
    bound = bool(licensed_deployment and local_deployment)
    base = {
        "customer": str(document.get("customer") or ""),
        "features": features,
        "issued_at": document.get("issued_at"),
        "expires_at": document.get("expires_at"),
        "contract_ref": document.get("contract_ref"),
        "deployment_id": licensed_deployment or None,
        "mode": str(document.get("mode") or "on_prem"),
        "source": source,
        "configured": True,
        "days_left": days_left,
        "unknown_features": unknown,
        "deployment_bound": bound,
    }

    if licensed_deployment and local_deployment and licensed_deployment != local_deployment:
        return LicenseStatus(
            edition=EDITION_COMMUNITY,
            reason="授权绑定的是另一个 DEPLOYMENT_ID（换机器/换部署需重新签发）",
            **base,
        )
    if licensed_deployment and not local_deployment:
        # 本机没配 DEPLOYMENT_ID：相当于未绑定，放行但提示（开发/演示便利）
        logger.warning("授权文件声明了 deployment_id，但本机未配置 DEPLOYMENT_ID，已跳过指纹校验")
    if expired:
        return LicenseStatus(
            edition=EDITION_COMMUNITY,
            expired=True,
            reason=f"授权已于 {document.get('expires_at')} 到期（降级为社区版，续期后重启即恢复）",
            **base,
        )
    if edition == EDITION_COMMUNITY:
        return LicenseStatus(edition=EDITION_COMMUNITY, valid=True, reason="", **base)

    return LicenseStatus(
        edition=EDITION_ENTERPRISE,
        active_features=features,
        valid=True,
        reason="",
        **base,
    )


# 进程内缓存：启动时算一次，`reload_license()` 换文件后重新读取
_cached: LicenseStatus | None = None


def _license_path() -> Path:
    raw = (settings.license_file or "").strip()
    return Path(raw).expanduser() if raw else Path("license.json")


def load_license(*, force: bool = False) -> LicenseStatus:
    """读取并校验授权；结果缓存在进程内。任何异常都降级，不抛给调用方。"""
    global _cached
    if _cached is not None and not force:
        return _cached

    path = _license_path()
    source = str(path)
    try:
        if not path.is_file():
            status = LicenseStatus(source=source, reason=f"未找到授权文件 {source}（社区版）")
            _cached = status
            return status
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("顶层必须是 JSON 对象")
    except (OSError, ValueError) as exc:
        status = LicenseStatus(
            source=source,
            configured=path.is_file(),
            reason=f"授权文件读取失败：{exc.__class__.__name__}",
        )
        _cached = status
        return status

    _cached = verify_document(
        document,
        public_key=parse_public_key(settings.license_public_key),
        deployment_id=settings.deployment_id,
        source=source,
    )
    return _cached


def reload_license() -> LicenseStatus:
    """换授权文件后调用（后台“刷新授权”按钮或运维重启，无需改代码）。"""
    return load_license(force=True)


def license_status() -> LicenseStatus:
    return load_license()


def current_edition() -> str:
    return license_status().edition


def active_features() -> frozenset[str]:
    return license_status().active_features


def has_feature(feature: str) -> bool:
    """功能位开关。社区版恒为 False；企业功能代码本身不在开源仓库（D6）。"""
    return feature in license_status().active_features


def log_startup_license() -> None:
    """启动自检输出一行：license=enterprise expires_in=365d。"""
    status = license_status()
    if status.valid:
        logger.info(
            "授权: license=%s customer=%s expires_in=%sd features=%s",
            status.edition,
            status.customer or "-",
            status.days_left if status.days_left is not None else "∞",
            ",".join(sorted(status.active_features)) or "-",
        )
        if status.days_left is not None and status.days_left <= EXPIRING_SOON_DAYS:
            logger.warning(
                "授权将在 %s 天后到期（%s），请及时续期；到期后仅关闭企业功能，对话不受影响",
                status.days_left,
                status.expires_at,
            )
        return
    logger.warning("授权: license=community（%s）；对话与社区版功能不受影响", status.reason)
