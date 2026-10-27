"""迁移执行器测试：命名规范、规划逻辑、真实库上的建库/升级/幂等。

集成用例需要 ``DATABASE_URL`` 指向可写的 PostgreSQL（会创建临时 schema 后删除），
未配置时自动跳过，保证纯逻辑用例在无库环境（CI 无 service）也能跑。
"""
import hashlib
import os
import uuid
from pathlib import Path

import asyncpg
import pytest

from app.core.migrate import (
    Migration,
    applied_checksums,
    apply_one,
    ensure_baseline,
    ensure_migrations_table,
    load_migrations,
    plan,
    read_db_version,
    run_sql_file,
)
from app.core.schema import SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "scripts" / "migrations"


def _dsn() -> str | None:
    """集成用例专用连接串。

    优先 ``TEST_DATABASE_URL``；回退到 ``DATABASE_URL`` 时**只允许本机地址**，
    避免在共享/生产库上误建临时 schema（.env 会被 app.core.config 注入 os.environ）。
    """
    dedicated = os.getenv("TEST_DATABASE_URL", "").strip()
    raw = dedicated or os.getenv("DATABASE_URL", "").strip()
    if not raw:
        return None
    dsn = raw.replace("postgresql+asyncpg://", "postgresql://")
    if dedicated:
        return dsn
    if "localhost" in dsn or "127.0.0.1" in dsn:
        return dsn
    return None


requires_db = pytest.mark.skipif(
    _dsn() is None, reason="需要 TEST_DATABASE_URL（或本机 DATABASE_URL）指向可写 PostgreSQL"
)


def _migration(version: str, checksum: str = "deadbeef") -> Migration:
    return Migration(
        version=version,
        name=f"{version}_test",
        path=Path(f"{version}_test.sql"),
        checksum=checksum,
        sql="",
    )


def test_schema_version_matches_init_sql() -> None:
    """SCHEMA_VERSION 必须与 init_db.sql 末尾写入的版本一致。"""
    sql = (REPO_ROOT / "scripts" / "init_db.sql").read_text(encoding="utf-8")
    assert f"('version', '{SCHEMA_VERSION}')" in sql


def test_repo_migrations_are_valid() -> None:
    """仓库内迁移文件命名合法、编号唯一递增，且不超过 SCHEMA_VERSION。"""
    items = load_migrations(MIGRATIONS)
    numbers = [item.number for item in items]
    assert numbers == sorted(numbers)
    assert len(numbers) == len(set(numbers))
    for item in items:
        assert item.number <= int(SCHEMA_VERSION), (
            f"{item.path.name} 的编号大于 SCHEMA_VERSION={SCHEMA_VERSION}，"
            "新增迁移必须同步 schema.py 与 init_db.sql"
        )


def test_cli_entrypoint_matches_core() -> None:
    """``python -m app.migrate`` 必须可用：短路径入口不能与实现脱钩。"""
    import app.core.migrate as core
    import app.migrate as cli

    assert cli.main is core.main


def test_load_migrations_sorts_and_checksums(tmp_path: Path) -> None:
    # 用 write_bytes 避免 Windows 文本模式把 \n 写成 \r\n
    (tmp_path / "0008_second.sql").write_bytes(b"SELECT 2;\n")
    (tmp_path / "0007_first.sql").write_bytes(b"SELECT 1;\n")

    items = load_migrations(tmp_path)
    assert [item.version for item in items] == ["0007", "0008"]
    assert items[0].checksum == hashlib.sha256(b"SELECT 1;\n").hexdigest()


def test_load_migrations_normalizes_crlf(tmp_path: Path) -> None:
    """CRLF 与 LF 必须算出同一个 checksum（跨平台检出场景）。"""
    (tmp_path / "0007_lf.sql").write_bytes(b"SELECT 1;\n")
    lf = load_migrations(tmp_path)[0].checksum
    (tmp_path / "0007_lf.sql").write_bytes(b"SELECT 1;\r\n")
    crlf = load_migrations(tmp_path)[0].checksum
    assert lf == crlf == hashlib.sha256(b"SELECT 1;\n").hexdigest()


def test_load_migrations_rejects_bad_name(tmp_path: Path) -> None:
    (tmp_path / "7_bad.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_migrations(tmp_path)


def test_load_migrations_rejects_duplicate_number(tmp_path: Path) -> None:
    (tmp_path / "0007_a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (tmp_path / "0007_b.sql").write_text("SELECT 2;\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_migrations(tmp_path)


def test_load_migrations_requires_directory(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        load_migrations(tmp_path / "missing")


def test_plan_splits_pending_and_bootstrap() -> None:
    pending, bootstrap = plan([_migration("0006"), _migration("0007")], "6", {})
    assert [m.version for m in pending] == ["0007"]
    assert [m.version for m in bootstrap] == ["0006"]


def test_plan_all_pending_on_empty_db() -> None:
    pending, bootstrap = plan([_migration("0007")], None, {})
    assert [m.version for m in pending] == ["0007"]
    assert bootstrap == []


def test_plan_rejects_modified_history() -> None:
    """已记录的迁移文件被改动（checksum 变化）时必须中止。"""
    with pytest.raises(SystemExit):
        plan([_migration("0007", checksum="new")], "7", {"0007": "old"})


def test_plan_skips_already_recorded() -> None:
    pending, bootstrap = plan([_migration("0007", checksum="same")], "7", {"0007": "same"})
    assert pending == [] and bootstrap == []


@pytest.mark.asyncio
@requires_db
async def test_empty_db_bootstraps_then_migrates_without_data_loss(tmp_path: Path) -> None:
    """空库建到最新结构 → 写入业务数据 → 执行增量 → 数据仍在、版本推进。"""
    dsn = _dsn()
    assert dsn
    schema = f"mig_test_{uuid.uuid4().hex[:8]}"

    admin = await asyncpg.connect(dsn, statement_cache_size=0)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    await admin.close()

    conn = await asyncpg.connect(
        dsn,
        statement_cache_size=0,
        # public 尾随：pg_trgm 的 gin_trgm_ops 在 public schema，必须能解析
        server_settings={"search_path": f"{schema},public"},
    )
    try:
        built = await ensure_baseline(conn, dry_run=False)
        assert built == SCHEMA_VERSION

        await conn.execute(
            "INSERT INTO scenes (code, display_name) VALUES ('mig_keep', '保留我')"
        )
        await ensure_migrations_table(conn)

        # 模拟下一个版本：新增一张表（幂等写法，符合迁移规范）
        # 编号取 SCHEMA_VERSION+1：基线已建到最新版，演练用的是“再下一个版本”
        next_version = f"{int(SCHEMA_VERSION) + 1:04d}"
        (tmp_path / f"{next_version}_test_usage.sql").write_text(
            "CREATE TABLE IF NOT EXISTS test_usage (\n"
            "    id INT PRIMARY KEY,\n"
            "    tokens BIGINT NOT NULL DEFAULT 0\n"
            ");\n",
            encoding="utf-8",
        )
        items = load_migrations(tmp_path)

        current = await read_db_version(conn)
        pending, bootstrap = plan(items, current, await applied_checksums(conn))
        assert [m.version for m in pending] == [next_version]
        assert bootstrap == []

        # dry-run 不落库
        await run_sql_file(conn, pending[0].sql, dry_run=True)
        assert await conn.fetchval("SELECT to_regclass('test_usage')") is None

        for item in pending:
            await apply_one(conn, item)

        assert await read_db_version(conn) == str(int(SCHEMA_VERSION) + 1)
        assert await conn.fetchval("SELECT to_regclass('test_usage')") is not None
        assert await conn.fetchval("SELECT count(*) FROM scenes WHERE code = 'mig_keep'") == 1

        # 再跑一次：无待执行，幂等
        pending_again, _ = plan(items, await read_db_version(conn), await applied_checksums(conn))
        assert pending_again == []
    finally:
        await conn.close()
        admin = await asyncpg.connect(dsn, statement_cache_size=0)
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


# ---------- G1 演练：自造存量库（500 消息 / 3 知识库 / 20 工单） ----------

G1_SEED_SQL = """
-- 3 个知识库 + 30 篇文档
INSERT INTO knowledge_bases (namespace, display_name, description)
SELECT 'g1_kb_' || i, 'G1 演练库 ' || i, 'G1 演练知识库' FROM generate_series(1, 3) i;

INSERT INTO documents (title, rag_namespace, status, chunk_count, size_bytes, kind)
SELECT 'g1-doc-' || i || '.md', 'g1_kb_' || (1 + i % 3), 'ready', 12, 2048, 'upload'
FROM generate_series(1, 30) i;

-- 20 个客户 + 100 个匿名访客
INSERT INTO customer_users (username, password_hash)
SELECT 'g1_user_' || i, 'x' FROM generate_series(1, 20) i;

INSERT INTO visitors (scene_id, external_key)
SELECT (SELECT id FROM scenes WHERE is_default), 'g1_visitor_' || i
FROM generate_series(1, 100) i;

-- 120 个会话（100 匿名 + 20 客户）
INSERT INTO sessions (scene_id, visitor_id, title)
SELECT (SELECT id FROM scenes WHERE is_default), id, 'g1 匿名会话 ' || external_key
FROM visitors WHERE external_key LIKE 'g1_visitor_%';

INSERT INTO sessions (scene_id, user_id, title, status)
SELECT (SELECT id FROM scenes WHERE is_default), id, 'g1 客户会话 ' || username, 'closed'
FROM customer_users WHERE username LIKE 'g1_user_%';

-- 500 条消息，按会话轮转
WITH s AS (
    SELECT id, row_number() OVER (ORDER BY id) AS rn FROM sessions WHERE title LIKE 'g1 %'
)
INSERT INTO messages (session_id, role, content, tokens, latency_ms, routed_to)
SELECT s.id,
       CASE WHEN g % 2 = 1 THEN 'user' ELSE 'assistant' END,
       'g1 第 ' || g || ' 条消息：' || repeat('演练内容', 20),
       40 + g % 60,
       CASE WHEN g % 2 = 1 THEN NULL ELSE 800 + g END,
       CASE WHEN g % 2 = 1 THEN NULL ELSE 'default_assistant' END
FROM generate_series(1, 500) g
JOIN s ON s.rn = 1 + g % 120;

-- 20 个工单
INSERT INTO tickets (ticket_no, category, urgency, status, title, content, updates)
SELECT 'G1-' || lpad(i::text, 4, '0'),
       (ARRAY['订单', '售后', '技术'])[1 + i % 3],
       (ARRAY['low', 'medium', 'high'])[1 + i % 3],
       (ARRAY['open', 'in_progress', 'escalated', 'resolved', 'closed'])[1 + i % 5],
       'g1 工单 #' || i,
       '演练工单正文 ' || i,
       jsonb_build_array(jsonb_build_object('note', 'g1 ' || i))
FROM generate_series(1, 20) i;

-- 60 条记忆 / 50 条链路 / 200 条埋点
INSERT INTO memories (visitor_id, memory_uid, type, content, importance)
SELECT (SELECT id FROM visitors WHERE external_key = 'g1_visitor_' || (1 + g % 100)),
       'g1_mem_' || g,
       (ARRAY['episodic', 'semantic', 'perceptual'])[1 + g % 3],
       'g1 记忆内容 ' || g,
       0.3 + (g % 7) * 0.1
FROM generate_series(1, 60) g;

INSERT INTO request_traces (trace_id, scene_code, session_id, outcome, total_latency_ms, spans)
SELECT 'g1-trace-' || g,
       'default',
       (SELECT id FROM sessions WHERE title LIKE 'g1 %' ORDER BY id OFFSET g % 120 LIMIT 1),
       'answered',
       500 + g,
       jsonb_build_array(jsonb_build_object('name', 'router', 'ms', 30))
FROM generate_series(1, 50) g;

INSERT INTO chat_events (trace_id, scene_code, message_chars, ext)
SELECT 'g1-ev-' || g, 'default', 20 + g, jsonb_build_object('platform', 'g1')
FROM generate_series(1, 200) g;

-- 其余业务配置少量打底
INSERT INTO tools (name, type, schema)
SELECT 'g1_tool_' || i, 'custom', '{"type":"object","properties":{}}'::jsonb
FROM generate_series(1, 5) i;

INSERT INTO agents (scene_id, name, source)
SELECT (SELECT id FROM scenes WHERE is_default), 'g1_agent_' || i, 'dynamic'
FROM generate_series(1, 3) i;

INSERT INTO integrations (name, public_code)
SELECT 'g1_int_' || i, 'g1_ic_' || i FROM generate_series(1, 2) i;

INSERT INTO integration_keys (integration_id, key_hash, hint)
SELECT id, 'g1_hash_' || public_code, 'g1' FROM integrations WHERE name LIKE 'g1_int_%';

INSERT INTO canned_scripts (scene_id, key, title, reply_text)
SELECT (SELECT id FROM scenes WHERE is_default), 'g1_script_' || i, 'G1 话术 ' || i, '好的'
FROM generate_series(1, 3) i;

INSERT INTO audit_logs (action, target_type, payload)
SELECT 'g1.action.' || i, 'ticket', jsonb_build_object('i', i) FROM generate_series(1, 30) i;
"""

# 演练增量：模拟下一个真实版本。对象名一律带 rehearsal_ 前缀，
# 不与基线里的真实表/列同名（同名会让“dry-run 未落库”的断言失真）。
G1_MIGRATION_SQL = """
ALTER TABLE chat_events ADD COLUMN IF NOT EXISTS rehearsal_flag BOOLEAN;

CREATE TABLE IF NOT EXISTS rehearsal_metrics (
    id          INT  PRIMARY KEY,
    scene_code  VARCHAR(32) NOT NULL DEFAULT 'default',
    score       REAL        NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_rehearsal_metrics_scene
    ON rehearsal_metrics(scene_code, id DESC);
"""

# 演练要有量的表（G1 验收口径）
G1_EXPECTED = {
    "messages": 500,
    "tickets": 20,
    "knowledge_bases": 3,   # 只看 g1_kb_*
    "documents": 30,
    "sessions": 120,
}

# 迁移元数据表：它们本来就该变（版本推进 + 执行记录），不算数据变化
G1_META_TABLES = {"schema_meta", "schema_migrations"}


async def _table_columns(conn: asyncpg.Connection) -> dict[str, list[str]]:
    rows = await conn.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() ORDER BY table_name, ordinal_position"
    )
    columns: dict[str, list[str]] = {}
    for r in rows:
        columns.setdefault(r["table_name"], []).append(r["column_name"])
    return columns


async def _snapshot(
    conn: asyncpg.Connection, columns: dict[str, list[str]]
) -> dict[str, tuple[int, str]]:
    """每张表 (行数, 全行内容 md5)。

    列清单取自迁移前：新增列不会让旧列的内容摘要变化，"数据零丢失"才可严格比对。
    """
    snapshot: dict[str, tuple[int, str]] = {}
    for table, cols in sorted(columns.items()):
        expr = "ROW(" + ", ".join(f'"{c}"' for c in cols) + ")"
        row = await conn.fetchrow(
            f'SELECT count(*) AS n, coalesce(md5(string_agg({expr}::text, \'|\' '
            f'ORDER BY {expr}::text)), \'\') AS h FROM "{table}"'
        )
        assert row is not None
        snapshot[table] = (row["n"], row["h"])
    return snapshot


@pytest.mark.asyncio
@requires_db
async def test_g1_rehearsal_upgrade_keeps_data(tmp_path: Path) -> None:
    """G1 演练：自造存量库（500 消息/3 知识库/20 工单）→ 增量迁移 → 零丢失。

    基线随 init_db.sql 演进，演练固定用“再下一个版本”的增量（编号 = SCHEMA_VERSION+1），
    因此不需要随 SCHEMA_VERSION 改测试逻辑，只需保证编号 > 基线版本。
    逐表比对 行数 + 全行内容 md5（而不是只看计数），并验证 dry-run 不落库、
    二次执行幂等、迁移记录与版本号同时推进。
    """
    dsn = _dsn()
    assert dsn
    schema = f"g1_rehearsal_{uuid.uuid4().hex[:8]}"

    admin = await asyncpg.connect(dsn, statement_cache_size=0)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    await admin.close()

    conn = await asyncpg.connect(
        dsn,
        statement_cache_size=0,
        server_settings={"search_path": f"{schema},public"},
    )
    try:
        # 1) 建存量库并灌入业务数据
        assert await ensure_baseline(conn, dry_run=False) == SCHEMA_VERSION
        await conn.execute(G1_SEED_SQL)
        await ensure_migrations_table(conn)

        g1_kbs = await conn.fetchval("SELECT count(*) FROM knowledge_bases WHERE namespace LIKE 'g1_kb_%'")
        counts = {
            "messages": await conn.fetchval("SELECT count(*) FROM messages"),
            "tickets": await conn.fetchval("SELECT count(*) FROM tickets"),
            "knowledge_bases": g1_kbs,
            "documents": await conn.fetchval("SELECT count(*) FROM documents"),
            "sessions": await conn.fetchval("SELECT count(*) FROM sessions WHERE title LIKE 'g1 %'"),
        }
        assert counts == G1_EXPECTED, f"演练数据量不对：{counts}"

        columns = await _table_columns(conn)
        before = await _snapshot(conn, columns)
        assert before["messages"][0] == 500

        # 2) 增量：dry-run 必须不落库
        next_version = f"{int(SCHEMA_VERSION) + 1:04d}"
        (tmp_path / f"{next_version}_g1_rehearsal.sql").write_text(G1_MIGRATION_SQL, encoding="utf-8")
        items = load_migrations(tmp_path)
        current = await read_db_version(conn)
        pending, bootstrap = plan(items, current, await applied_checksums(conn))
        assert [m.version for m in pending] == [next_version] and bootstrap == []

        await run_sql_file(conn, pending[0].sql, dry_run=True)
        assert await _snapshot(conn, columns) == before, "dry-run 修改了数据"
        assert await conn.fetchval("SELECT to_regclass('rehearsal_metrics')") is None
        assert await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'chat_events' AND column_name = 'rehearsal_flag'"
        ) is None, "dry-run 留下了新列"

        # 3) 正式执行：数据必须逐表逐行一致
        for item in pending:
            await apply_one(conn, item)

        after = await _snapshot(conn, columns)
        drift = [t for t in before if t not in G1_META_TABLES and before[t] != after[t]]
        assert not drift, f"迁移后业务数据变了：{drift}"
        # 元数据表只能按预期变：版本推进 + 新增一条执行记录
        assert before["schema_meta"][0] == after["schema_meta"][0] == 1
        assert before["schema_migrations"][0] == 0 and after["schema_migrations"][0] == 1

        business_tables = [t for t in before if t not in G1_META_TABLES]
        print(f"\n[G1] 迁移前后逐表一致（行数 / 全行 md5），共 {len(business_tables)} 张业务表：")
        for table in business_tables:
            count, digest = before[table]
            if count:
                print(f"  {table:<20} {count:>5} 行  md5={digest[:12]}")

        # 4) 结构推进 + 记录写入 + 二次执行幂等
        assert await read_db_version(conn) == str(int(SCHEMA_VERSION) + 1)
        assert await conn.fetchval("SELECT to_regclass('rehearsal_metrics')") is not None
        records = await conn.fetch("SELECT version, checksum FROM schema_migrations")
        assert [(r["version"], r["checksum"]) for r in records] == [
            (next_version, items[0].checksum)
        ]

        pending_again, _ = plan(items, await read_db_version(conn), await applied_checksums(conn))
        assert pending_again == [], "迁移不幂等：二次仍有待执行"
        assert await conn.fetchval("SELECT count(*) FROM messages") == 500
    finally:
        await conn.close()
        admin = await asyncpg.connect(dsn, statement_cache_size=0)
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()
