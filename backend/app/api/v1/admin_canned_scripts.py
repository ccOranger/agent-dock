"""管理后台：高频固定话术（硬模板）维护

话术 = 命中规则（问法主干 / 关键词）+ 逐字应答正文。命中即在路由之前直接应答，
所以这里的每次改动都影响"哪句客户话会被一句固定模板接住"，风险方向是误吞：
- 后台不提供正则（避免运营写出灾难性回溯或过宽规则）；
- 关键词最少 2 字（单字关键词几乎必然吞掉全部句子）；
- 提供 /match 命中测试，保存前先拿真实问法验一遍。

种子三条（is_builtin，由 scripts/seed_data.py 建库时写入）可改文案、可停用，但不可删除：
运行时只认这张表，删掉探听拒答就等于把"系统提示词"这类提问重新交回模型自由发挥。
"""
import uuid

from fastapi import APIRouter, Depends, Query

from app.agents.canned_scripts import match_canned_script, normalize_query
from app.api.deps import require_admin, require_staff
from app.core.exceptions import Conflict, NotFound, ParamError
from app.models.repositories import AuditRepo, CannedScriptRepo, SceneRepo
from app.schemas.api import (
    CannedScriptCreate,
    CannedScriptMatchRequest,
    CannedScriptToggle,
    CannedScriptUpdate,
)
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/admin/canned-scripts", tags=["admin-canned-scripts"])

_MAX_PATTERN_CHARS = 64
_MAX_KEYWORD_CHARS = 32


def _clean_items(values: list[str] | None, *, kind: str, max_len: int, min_len: int) -> list[str]:
    """去空白、剔除空项、按输入序去重，并校验单项长度（关键词过短会吞掉所有句子）。"""
    cleaned: list[str] = []
    for raw in values or []:
        item = (raw or "").strip()
        if not item:
            continue
        if len(item) > max_len:
            raise ParamError(f"{kind}过长（最多 {max_len} 字）: {item[:max_len]}…")
        # 与运行时同一套归一化（normalize_query）：否则校验时觉得合规、入库后永远匹配不上
        if len(normalize_query(item)) < min_len:
            raise ParamError(f"{kind}太短（去掉标点后至少 {min_len} 字）: {item}")
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


def _validate_rule_fields(patterns: list[str] | None, keywords: list[str] | None) -> tuple[list[str], list[str]]:
    pats = _clean_items(patterns, kind="问法", max_len=_MAX_PATTERN_CHARS, min_len=1)
    keys = _clean_items(keywords, kind="关键词", max_len=_MAX_KEYWORD_CHARS, min_len=2)
    if not pats and not keys:
        raise ParamError("问法与关键词至少要填一项，否则这条话术永远不会命中")
    return pats, keys


async def _resolve_scene_id(scene_code: str | None) -> str | None:
    """scene_code → scene_id；空表示全局话术，未知场景直接报错（静默当全局会串场）。"""
    if not scene_code:
        return None
    scene = await SceneRepo.get_by_code(scene_code)
    if scene is None:
        raise NotFound("场景不存在")
    return str(scene.id)


def _audit_target(key: str, scene_code: str | None) -> str:
    return f"{scene_code}:{key}" if scene_code else key


@router.get("", response_model=ApiResponse[list])
async def list_canned_scripts(
    scene_code: str | None = Query(None, description="只看某场景生效的全局行 + 场景覆盖行"),
    user=Depends(require_staff),
):
    """全量话术（含停用项），按优先级升序返回，与运行时判定顺序一致。"""
    rows = await CannedScriptRepo.list_all()
    if scene_code:
        scene_id = await _resolve_scene_id(scene_code)
        rows = [r for r in rows if r.scene_id is None or str(r.scene_id) == scene_id]
    return ApiResponse(data=[CannedScriptRepo.to_dict(r) for r in rows])


@router.post("", response_model=ApiResponse[dict])
async def create_canned_script(body: CannedScriptCreate, user=Depends(require_admin)):
    scene_id = await _resolve_scene_id(body.scene_code)
    if await CannedScriptRepo.get_by_key(body.key, scene_id) is not None:
        where = f"（场景 {body.scene_code}）" if body.scene_code else "（全局）"
        raise Conflict(f"话术标识已存在{where}: {body.key}")
    patterns, keywords = _validate_rule_fields(body.patterns, body.keywords)
    row = await CannedScriptRepo.create(
        scene_id=uuid.UUID(scene_id) if scene_id else None,
        key=body.key,
        title=body.title,
        category=body.category,
        patterns=patterns,
        keywords=keywords,
        reply_text=body.reply_text.strip(),
        priority=body.priority,
        enabled=body.enabled,
        is_builtin=False,
    )
    await AuditRepo.write(
        action="admin.canned_script.create",
        user_id=str(user.id),
        target_type="canned_script",
        target_id=_audit_target(row.key, body.scene_code),
    )
    return ApiResponse(data=CannedScriptRepo.to_dict(row))


@router.put("/{key}", response_model=ApiResponse[dict])
async def update_canned_script(
    key: str,
    body: CannedScriptUpdate,
    scene_code: str | None = Query(None, description="为空时定位全局行"),
    user=Depends(require_admin),
):
    scene_id = await _resolve_scene_id(scene_code)
    row = await CannedScriptRepo.get_by_key(key, scene_id)
    if row is None:
        raise NotFound("话术不存在")
    fields: dict = {}
    if body.patterns is not None or body.keywords is not None:
        # 两侧要合并校验：只改一侧时另一侧沿用已存值，避免把规则改成空规则
        patterns, keywords = _validate_rule_fields(
            body.patterns if body.patterns is not None else list(row.patterns or []),
            body.keywords if body.keywords is not None else list(row.keywords or []),
        )
        fields.update(patterns=patterns, keywords=keywords)
    if body.title is not None:
        fields["title"] = body.title.strip() or None
    if body.category is not None:
        fields["category"] = body.category
    if body.reply_text is not None:
        fields["reply_text"] = body.reply_text.strip()
    if body.priority is not None:
        fields["priority"] = body.priority
    updated = await CannedScriptRepo.update(key, scene_id, **fields)
    if updated is None:
        raise NotFound("话术不存在")
    await AuditRepo.write(
        action="admin.canned_script.update",
        user_id=str(user.id),
        target_type="canned_script",
        target_id=_audit_target(key, scene_code),
    )
    return ApiResponse(data=CannedScriptRepo.to_dict(updated))


@router.patch("/{key}/toggle", response_model=ApiResponse[dict])
async def toggle_canned_script(
    key: str,
    body: CannedScriptToggle,
    scene_code: str | None = Query(None, description="为空时定位全局行"),
    user=Depends(require_admin),
):
    scene_id = await _resolve_scene_id(scene_code)
    row = await CannedScriptRepo.set_enabled(key, body.enabled, scene_id)
    if row is None:
        raise NotFound("话术不存在")
    await AuditRepo.write(
        action="admin.canned_script.toggle",
        user_id=str(user.id),
        target_type="canned_script",
        target_id=_audit_target(key, scene_code),
    )
    return ApiResponse(data=CannedScriptRepo.to_dict(row))


@router.delete("/{key}", response_model=ApiResponse)
async def delete_canned_script(
    key: str,
    scene_code: str | None = Query(None, description="为空时定位全局行"),
    user=Depends(require_admin),
):
    scene_id = await _resolve_scene_id(scene_code)
    row = await CannedScriptRepo.get_by_key(key, scene_id)
    if row is None:
        raise NotFound("话术不存在")
    if row.is_builtin:
        raise ParamError("种子话术不可删除，可关闭启用开关使其不再生效")
    await CannedScriptRepo.delete(key, scene_id)
    await AuditRepo.write(
        action="admin.canned_script.delete",
        user_id=str(user.id),
        target_type="canned_script",
        target_id=_audit_target(key, scene_code),
    )
    return ApiResponse()


@router.post("/match", response_model=ApiResponse[dict])
async def match_canned_script_test(body: CannedScriptMatchRequest, user=Depends(require_staff)):
    """命中测试：用真实问法验证规则，不落库、不产生会话消息。

    返回命中的话术与归一化后的主干，便于判断"为什么没中 / 为什么误吞了业务问法"。
    命中只可能来自本表（代码里不做兜底），未命中就是本轮会正常走路由。
    传 scene_code 时按运行时的「全局 + 场景覆盖」合并规则测试。
    """
    scene_id = await _resolve_scene_id(body.scene_code)
    hit = await match_canned_script(body.query, scene_id)
    rows = {r.key: r for r in await CannedScriptRepo.list_enabled(scene_id)}
    matched_row = rows.get(hit.key) if hit else None
    return ApiResponse(
        data={
            "query": body.query,
            "scene_code": body.scene_code,
            "normalized": normalize_query(body.query),
            "matched": bool(hit),
            "key": hit.key if hit else None,
            "category": hit.category if hit else None,
            "title": matched_row.title if matched_row else None,
            "reply_text": hit.text if hit else None,
            "rule_snapshot": [CannedScriptRepo.to_dict(r) for r in rows.values()][:50],
        }
    )
