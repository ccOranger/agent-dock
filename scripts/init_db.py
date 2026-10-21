"""初始化数据库：执行 scripts/init_db.sql（幂等，可重复运行）。

用于容器首启/空库部署：建表、扩展、知识库命名空间种子与 schema 版本。
整脚本包在一个事务里，逐条用 savepoint 执行：命中 "对象已存在"（SQLSTATE
42P07/42710/42P06）只回滚该条并继续，其余错误回滚整脚本并非零退出，
避免留下半套 schema。本脚本不做 ALTER/数据迁移；旧库请直接全量重建。

用法（DATABASE_URL 指向目标库）：
    python ../scripts/init_db.py
"""
import asyncio
import os
import re

import asyncpg

SQL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "init_db.sql")

# PostgreSQL SQLSTATE：重复表/索引、重复对象（扩展等）、重复 schema
IGNORED_SQLSTATES = {"42P07", "42710", "42P06"}


def dsn_from_env() -> str:
    """把 SQLAlchemy 的 postgresql+asyncpg:// 还原成 asyncpg 可用的 DSN。"""
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        raise SystemExit("DATABASE_URL 未配置，无法初始化数据库")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


def split_statements(sql: str) -> list[str]:
    """去掉整行注释后按分号切分（本项目 SQL 不含函数体，字符串中也不含分号）。"""
    body = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    return [stmt.strip() for stmt in body.split(";") if stmt.strip()]


async def main() -> None:
    with open(SQL_PATH, encoding="utf-8") as fh:
        statements = split_statements(fh.read())

    conn = await asyncpg.connect(dsn_from_env())
    applied = skipped = 0
    try:
        # 外层事务保证原子性；内层 savepoint 让“已存在”只回滚单条语句
        async with conn.transaction():
            for stmt in statements:
                try:
                    async with conn.transaction():
                        await conn.execute(stmt)
                    applied += 1
                except asyncpg.PostgresError as exc:
                    if exc.sqlstate not in IGNORED_SQLSTATES:
                        raise
                    skipped += 1
        print(f"✅ init_db 完成：执行 {applied} 条，跳过 {skipped} 条已存在对象")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
