"""场景身份/能力话术初始化：把全局模板复制成本场景行。

为什么复制而不是运行时继承：
- 运行时同一 key 是"场景行覆盖全局行"。没有场景行时，所有场景都会回答平台统一文案，
  运营改一个场景就要连累全部场景；复制之后每个场景各自独立，改文案不影响别人。
- 复制只发生在"场景还没有这个 key"时，运营改过或停用过的行永远不覆盖。

调用方：
- ``admin_scenes.create_scene``：新建场景后补齐；
- ``scripts/seed_data.py`` / ``scripts/backfill_scene_canned.py``：存量场景幂等补齐。

失败策略：调用方只告警不回滚（与"新场景自动授权接入方"一致），
少一条话术不该让已创建的场景消失。
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.canned_scripts import (
    ANSWER_SOURCE_TEXT,
    KEY_CAPABILITY,
    KEY_IDENTITY,
    SEED_RULES,
)
from app.core.database import SessionLocal
from app.models.entities import CannedScript, Scene

logger = logging.getLogger(__name__)

# 需要按场景复制的模板话术：身份与能力两类随场景变化，探听拒答（probing）保持全局一份
TEMPLATE_KEYS = (KEY_IDENTITY, KEY_CAPABILITY)

_TITLE_BY_KEY = {KEY_IDENTITY: "身份介绍", KEY_CAPABILITY: "能力介绍"}


async def _ensure_global_templates(db: AsyncSession) -> None:
    """确保全局模板行存在：全新库若跳过 seed，也应有可复制的模板。"""
    existing = set(
        (
            await db.execute(
                select(CannedScript.key).where(
                    CannedScript.scene_id.is_(None), CannedScript.key.in_(TEMPLATE_KEYS)
                )
            )
        )
        .scalars()
        .all()
    )
    for rule in SEED_RULES:
        if rule.key not in TEMPLATE_KEYS or rule.key in existing:
            continue
        db.add(
            CannedScript(
                scene_id=None,
                key=rule.key,
                title=_TITLE_BY_KEY.get(rule.key, rule.key),
                category=rule.category,
                patterns=sorted(rule.patterns),
                keywords=list(rule.keywords),
                reply_text=rule.text,
                answer_source=ANSWER_SOURCE_TEXT,
                kb_namespaces=[],
                priority=rule.priority,
                enabled=True,
                is_builtin=True,
            )
        )


async def ensure_scene_templates(db: AsyncSession, scene_id: uuid.UUID | str) -> list[str]:
    """给指定场景补齐身份/能力话术（同会话内执行，不提交）。

    返回本次新建的 key 列表；已存在的 key 一律跳过（运营配置优先）。
    """
    scene_uuid = uuid.UUID(str(scene_id))
    await _ensure_global_templates(db)
    await db.flush()

    templates = (
        (
            await db.execute(
                select(CannedScript).where(
                    CannedScript.scene_id.is_(None), CannedScript.key.in_(TEMPLATE_KEYS)
                )
            )
        )
        .scalars()
        .all()
    )
    if not templates:
        return []

    existing = set(
        (
            await db.execute(
                select(CannedScript.key).where(CannedScript.scene_id == scene_uuid)
            )
        )
        .scalars()
        .all()
    )
    created: list[str] = []
    for row in templates:
        if row.key in existing:
            continue
        db.add(
            CannedScript(
                scene_id=scene_uuid,
                key=row.key,
                title=row.title or _TITLE_BY_KEY.get(row.key, row.key),
                category=row.category,
                patterns=list(row.patterns or []),
                keywords=list(row.keywords or []),
                reply_text=row.reply_text or "",
                answer_source=row.answer_source or ANSWER_SOURCE_TEXT,
                kb_namespaces=list(row.kb_namespaces or []),
                priority=row.priority,
                enabled=True,
                # 场景行是运营可改可删的副本，不继承"内置不可删"标记
                is_builtin=False,
            )
        )
        created.append(row.key)
    return created


async def ensure_scene_templates_committed(scene_id: uuid.UUID | str) -> list[str]:
    """独立事务版本：供后台创建场景后调用（失败由调用方告警，不回滚场景）。"""
    async with SessionLocal() as db:
        created = await ensure_scene_templates(db, scene_id)
        await db.commit()
    return created


async def backfill_all_scenes() -> dict[str, list[str]]:
    """遍历全部场景补齐模板话术（幂等，独立事务）；返回 {scene_code: 新建 key 列表}。"""
    result: dict[str, list[str]] = {}
    async with SessionLocal() as db:
        scenes = (await db.execute(select(Scene))).scalars().all()
        await _ensure_global_templates(db)
        for scene in scenes:
            created = await ensure_scene_templates(db, scene.id)
            if created:
                result[scene.code] = created
        await db.commit()
    return result
