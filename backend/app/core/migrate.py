"""数据库增量迁移执行器。

约定（与 ``scripts/README.md`` 一致）：

- ``scripts/init_db.sql`` 是**最新结构**的唯一来源：空库直接建到最新版，
  不重放历史迁移，避免"新库跑一遍历史 DDL"这种无意义风险；
- ``scripts/migrations/NNNN_slug.sql`` 是**增量**，只增不改，服务已有数据的存量库；
- 版本以 ``schema_meta.version`` 为准，``schema_migrations`` 只记录增量执行历史；
- 增量文件必须幂等（``IF NOT EXISTS`` / ``ON CONFLICT``）：这样"最新结构上再跑一遍
  全部增量"结果不变，parity 测试才有意义，误重放也不会炸。

为什么不做成 Alembic：本项目 DDL 全部是手写幂等 SQL，且已在
``scripts/init_db.py`` / ``rebuild_and_seed.py`` 两处依赖这套写法；
引入 Alembic 需要 autogenerate 与实体模型对齐，收益不足以抵消改动面。

用法（backend 目录，DATABASE_URL 指向目标库）：

    python -m app.migrate --check      # 只读：当前版本 + 待执行列表（有待执行时退出码 1）
    python -m app.migrate --dry-run    # 事务内执行后回滚，验证 SQL 可跑
    python -m app.migrate --backup     # 有待执行时先 pg_dump
    python -m app.migrate              # 顺序执行并记录
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import asyncpg

# 文件名即版本号：0007_usage_and_eval.sql → version="0007"
MIGRATION_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     VARCHAR(8) PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    checksum    CHAR(64) NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    version: str          # 四位编号，如 "0007"
    name: str             # 文件名去扩展名，如 "0007_usage_and_eval"
    path: Path
    checksum: str
    sql: str

    @property
    def number(self) -> int:
        return int(self.version)


def repo_root() -> Path:
    """仓库根：本地是 <repo>/，容器里 backend 代码在 /app/backend/。"""
    return Path(__file__).resolve().parents[3]


def migrations_dir() -> Path:
    override = os.getenv("MIGRATIONS_DIR", "").strip()
    return Path(override) if override else repo_root() / "scripts" / "migrations"


def init_sql_path() -> Path:
    override = os.getenv("INIT_DB_SQL", "").strip()
    return Path(override) if override else repo_root() / "scripts" / "init_db.sql"


def _dotenv_database_url() -> str:
    """本地开发只写了 ``backend/.env`` 时的兜底读取。

    ``dsn_from_env`` 只读进程环境（容器内由 compose 注入）；本地直跑
    ``python -m app.migrate`` 时没有人先加载 .env，这里按候选路径读一次，
    不写回 os.environ，避免污染其他配置来源。
    """
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - python-dotenv 是硬依赖，仅容错
        return ""
    for path in (repo_root() / "backend" / ".env", repo_root() / ".env"):
        if not path.is_file():
            continue
        value = (dotenv_values(path).get("DATABASE_URL") or "").strip()
        if value:
            return value
    return ""


def dsn_from_env() -> str:
    """把 SQLAlchemy 的 postgresql+asyncpg:// 还原成 asyncpg 可用 DSN。"""
    raw = os.getenv("DATABASE_URL", "").strip() or _dotenv_database_url()
    if not raw:
        raise SystemExit(
            "DATABASE_URL 未配置，无法执行迁移（可导出环境变量，或写入 backend/.env）"
        )
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


def load_migrations(directory: Path | None = None) -> list[Migration]:
    """读取迁移目录，按编号升序返回；编号重复或文件名不合法直接报错。"""
    directory = directory or migrations_dir()
    if not directory.is_dir():
        raise SystemExit(
            f"迁移目录不存在：{directory}\n"
            "容器部署请确认 Dockerfile 已 COPY scripts/migrations。"
        )

    items: list[Migration] = []
    seen: dict[str, str] = {}
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            raise SystemExit(f"迁移文件名不合法：{path.name}（应为 NNNN_slug.sql）")
        version, slug = match.groups()
        if version in seen:
            raise SystemExit(f"迁移编号重复：{version}（{seen[version]} 与 {path.name}）")
        seen[version] = path.name
        # Windows 检出任可能把 LF 转成 CRLF；checksum 必须跨平台一致，
        # 否则同一份迁移在 Linux 执行过、Windows 再执行就会报"文件被修改"。
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        items.append(
            Migration(
                version=version,
                name=path.stem,
                path=path,
                checksum=hashlib.sha256(raw).hexdigest(),
                sql=raw.decode("utf-8"),
            )
        )
    items.sort(key=lambda m: m.number)
    return items


async def ensure_migrations_table(conn: asyncpg.Connection) -> None:
    await conn.execute(MIGRATIONS_TABLE_SQL)


async def _schema_meta_exists(conn: asyncpg.Connection) -> bool:
    """只看 current_schema：避免 to_regclass 回退到 public 造成误判。"""
    found = await conn.fetchval(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = 'schema_meta'"
    )
    return found is not None


async def read_db_version(conn: asyncpg.Connection) -> str | None:
    """当前版本取 ``schema_meta``；``schema_migrations`` 的最大编号只作兜底。

    迁移表可能尚未创建（空库或刚建库），此时按"无增量记录"处理，
    避免调用方必须先建表才能读版本。
    """
    # 空库/刚建库时 schema_meta 还不存在，必须先探测再查询；
    # 否则 asyncpg 会直接抛 UndefinedTableError，走不到 init_db.sql 基线初始化。
    meta = None
    if await _schema_meta_exists(conn):
        meta = await conn.fetchval("SELECT value FROM schema_meta WHERE key = 'version'")
    has_table = await conn.fetchval(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = 'schema_migrations'"
    )
    applied = await conn.fetchval("SELECT max(version) FROM schema_migrations") if has_table else None
    candidates = [v for v in (meta, applied) if v]
    if not candidates:
        return None
    return str(max(int(v) for v in candidates))


async def applied_checksums(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch("SELECT version, checksum FROM schema_migrations")
    return {r["version"]: r["checksum"] for r in rows}


def plan(
    migrations: list[Migration], current: str | None, applied: dict[str, str]
) -> tuple[list[Migration], list[Migration]]:
    """返回 (待执行, 需补记录)。

    - 待执行：编号大于当前版本；
    - 需补记录：编号不大于当前版本但库里没有记录（存量库首次接入迁移体系）。
    补记录只写历史，不执行 SQL。
    """
    cur = int(current) if current else 0
    pending: list[Migration] = []
    bootstrap: list[Migration] = []
    for item in migrations:
        recorded = applied.get(item.version)
        if recorded is not None:
            if recorded != item.checksum:
                raise SystemExit(
                    f"迁移文件被修改：{item.path.name}\n"
                    f"已记录 checksum={recorded}，当前={item.checksum}。\n"
                    "历史迁移一经发布不可修改；确有修正请新增下一个编号。"
                )
            continue
        if item.number > cur:
            pending.append(item)
        else:
            bootstrap.append(item)
    return pending, bootstrap


async def bootstrap_records(conn: asyncpg.Connection, items: list[Migration]) -> None:
    for item in items:
        await conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum) VALUES ($1, $2, $3) "
            "ON CONFLICT (version) DO NOTHING",
            item.version,
            item.name,
            item.checksum,
        )


async def run_sql_file(conn: asyncpg.Connection, sql: str, *, dry_run: bool) -> None:
    """整段执行一个迁移文件。

    用 simple query 一次性发送多语句（asyncpg 在无参数时走 simple 协议），
    与 ``rebuild_and_seed.py`` 同样适配云库事务级代理。
    """
    if dry_run:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(sql)
        finally:
            await tx.rollback()
        return
    async with conn.transaction():
        await conn.execute(sql)


async def apply_one(conn: asyncpg.Connection, item: Migration) -> None:
    async with conn.transaction():
        await conn.execute(item.sql)
        await conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum) VALUES ($1, $2, $3)",
            item.version,
            item.name,
            item.checksum,
        )
        await conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('version', $1) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            str(item.number),
        )


async def ensure_baseline(conn: asyncpg.Connection, *, dry_run: bool) -> str | None:
    """空库时用 init_db.sql 建到最新结构，返回建完后的版本号。

    判定空库只看 **current_schema()**（search_path 第一个 schema），
    不能用 ``to_regclass('schema_meta')``：后者会回退到 public，
    在临时 schema 测试里会误判成"已有库"。
    """
    if await _schema_meta_exists(conn):
        return None

    path = init_sql_path()
    if not path.is_file():
        raise SystemExit(f"空库需要初始化脚本，但文件不存在：{path}")
    sql = path.read_text(encoding="utf-8")
    print(f"[migrate] 空库：执行 {path.name} 建立最新结构（幂等）")
    await run_sql_file(conn, sql, dry_run=dry_run)
    if dry_run:
        return None
    return await read_db_version(conn)


def backup(dsn: str) -> Path | None:
    """迁移前备份：pg_dump 自定义格式，输出到 BACKUP_DIR（默认 <repo>/backups）。"""
    out_dir = Path(os.getenv("BACKUP_DIR", "").strip() or repo_root() / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"pre-migrate-{stamp}.dump"
    try:
        subprocess.run(
            ["pg_dump", "--format=custom", f"--file={target}", "--dbname", dsn],
            check=True,
        )
    except FileNotFoundError:
        print("[migrate] 未找到 pg_dump，跳过自动备份；请确认已在别处备份。", file=sys.stderr)
        return None
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"pg_dump 失败（退出码 {exc.returncode}），已中止迁移") from exc
    print(f"[migrate] 已备份到 {target}")
    return target


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.migrate", description="执行数据库增量迁移"
    )
    parser.add_argument("--check", action="store_true", help="只检查，不执行（有待执行时退出码 1）")
    parser.add_argument("--dry-run", action="store_true", help="事务内执行后回滚，验证 SQL")
    parser.add_argument("--backup", action="store_true", help="执行前 pg_dump")
    args = parser.parse_args(argv)
    if args.check and args.dry_run:
        raise SystemExit("--check 与 --dry-run 不能同时使用")

    migrations = load_migrations()
    dsn = dsn_from_env()
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        await ensure_migrations_table(conn)
        current = await read_db_version(conn)
        if current is None:
            print("[migrate] 空库：将先用 init_db.sql 建立最新结构")
            current = "0"

        applied = await applied_checksums(conn)
        pending, bootstrap = plan(migrations, current, applied)

        print(f"[migrate] 数据库版本={current} 迁移文件={len(migrations)} 待执行={len(pending)}")
        for item in pending:
            print(f"  - {item.path.name}")
        if bootstrap:
            print(f"[migrate] 补写历史记录 {len(bootstrap)} 条（不执行 SQL）")

        if args.check:
            return 1 if pending else 0

        if args.dry_run:
            await ensure_baseline(conn, dry_run=True)
            for item in pending:
                print(f"[migrate] dry-run: {item.path.name}")
                await run_sql_file(conn, item.sql, dry_run=True)
            print("[migrate] dry-run 完成，未写入任何变更")
            return 0

        if not pending and await _schema_meta_exists(conn):
            await bootstrap_records(conn, bootstrap)
            print("[migrate] 已是最新版本，无需执行")
            return 0

        if args.backup and (pending or not await _schema_meta_exists(conn)):
            backup(dsn)

        built = await ensure_baseline(conn, dry_run=False)
        if built:
            current = built
            # 建库后重新规划：init_db.sql 已到最新版，历史增量全部作废
            applied = await applied_checksums(conn)
            pending, bootstrap = plan(migrations, current, applied)

        await bootstrap_records(conn, bootstrap)
        for item in pending:
            print(f"[migrate] 执行 {item.path.name}")
            await apply_one(conn, item)

        final = await read_db_version(conn)
        print(f"[migrate] 完成：数据库版本={final}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
