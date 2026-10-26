"""schema 版本守卫。

本项目不做增量迁移：``scripts/init_db.sql`` 只保留最新结构，换库直接全量重建。
旧库如果缺列/缺表，应该在启动时立刻给出“请全量重建”的明确提示，
而不是等第一轮对话写库时才报 UndefinedColumn。
"""
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.core.database import engine

# 与 scripts/init_db.sql 末尾 schema_meta 的 version 保持一致
SCHEMA_VERSION = "6"

_REBUILD_HINT = "本项目不做增量迁移，请全量重建：python scripts/rebuild_and_seed.py"


async def assert_schema_version() -> None:
    """在 FastAPI lifespan 启动阶段调用；版本不匹配直接阻断启动。"""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT value FROM schema_meta WHERE key = 'version'")
            )
            row = result.first()
    except ProgrammingError as exc:
        # schema_meta 不存在：典型旧库
        raise RuntimeError(f"数据库缺少 schema_meta 表。{_REBUILD_HINT}") from exc

    found = row[0] if row else "missing"
    if found != SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库 schema 版本不匹配（期望 {SCHEMA_VERSION}，实际 {found}）。{_REBUILD_HINT}"
        )
