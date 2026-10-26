"""补齐存量场景的身份/能力固定话术

场景化身份话术上线前创建的场景没有自己的 identity / capability 行，客户问"你是谁"
时会回落到平台统一文案。本脚本把这些场景补齐，逻辑与新建场景、seed 完全一致：

- 只补缺失的 key，运营改过或停用过的行一律不覆盖；
- 幂等，可重复执行；
- 全局模板缺失时会先用 SEED_RULES 建出来。

用法（backend 目录）：
    .\\.venv\\Scripts\\python.exe ..\\scripts\\backfill_scene_canned.py
    .\\.venv\\Scripts\\python.exe ..\\scripts\\backfill_scene_canned.py --dry-run
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import func, select  # noqa: E402

from app.core.database import SessionLocal, engine  # noqa: E402
from app.models.entities import CannedScript, Scene  # noqa: E402
from app.services.canned_templates import TEMPLATE_KEYS, backfill_all_scenes  # noqa: E402


async def _dry_run() -> None:
    """只统计会补多少条，不写库。"""
    async with SessionLocal() as db:
        scenes = (await db.execute(select(Scene.id, Scene.code))).all()
        created_count = 0
        for scene_id, code in scenes:
            existing = set(
                (
                    await db.execute(
                        select(CannedScript.key).where(CannedScript.scene_id == scene_id)
                    )
                )
                .scalars()
                .all()
            )
            missing = [k for k in TEMPLATE_KEYS if k not in existing]
            if missing:
                created_count += len(missing)
                print(f"   {code}: 待补 {', '.join(missing)}")
        print(f">> 干跑：{len(scenes)} 个场景，待补 {created_count} 条话术")


async def main(dry_run: bool) -> None:
    if dry_run:
        await _dry_run()
        return

    created = await backfill_all_scenes()
    if not created:
        print("[OK] 所有场景都已有身份/能力话术，无需补齐")
    else:
        total = sum(len(v) for v in created.values())
        for code, keys in created.items():
            print(f"   {code}: 新增 {', '.join(keys)}")
        print(f"[OK] 已为 {len(created)} 个场景补齐 {total} 条话术")

    async with SessionLocal() as db:
        total_rows = (
            await db.execute(select(func.count()).select_from(CannedScript))
        ).scalar_one()
    print(f">> 当前话术总行数：{total_rows}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="补齐存量场景身份/能力话术")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
