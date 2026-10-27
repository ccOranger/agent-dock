"""schema 版本守卫。

``scripts/init_db.sql`` 是空库的最新结构，``scripts/migrations/NNNN_*.sql`` 是存量库的
增量补丁。版本不匹配时要在启动阶段立刻给出可操作提示，而不是等第一轮对话
写库才报 UndefinedColumn。
"""
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.core.database import engine

# 与 scripts/init_db.sql 末尾 schema_meta 的 version 保持一致；
# 新增迁移后必须同步（tests/test_migrations.py 会做一致性断言）
SCHEMA_VERSION = "8"

_MIGRATE_HINT = (
    "请执行数据库迁移：python -m app.migrate（容器内："
    "docker compose run --rm backend python -m app.migrate）"
)


async def assert_schema_version() -> None:
    """在 FastAPI lifespan 启动阶段调用；版本不匹配直接阻断启动。"""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT value FROM schema_meta WHERE key = 'version'")
            )
            row = result.first()
    except ProgrammingError as exc:
        # schema_meta 不存在：全新未初始化的库
        raise RuntimeError(f"数据库尚未初始化（缺少 schema_meta）。{_MIGRATE_HINT}") from exc

    found = row[0] if row else "missing"
    if found != SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库 schema 版本不匹配（期望 {SCHEMA_VERSION}，实际 {found}）。{_MIGRATE_HINT}"
        )

