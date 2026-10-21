"""校验初始化脚本产出的 schema 与种子。

用法（backend 目录，先执行 scripts/rebuild_and_seed.py）：
    .\\.venv\\Scripts\\python.exe ..\\scripts\\verify_schema.py

校验分四层：
1. 结构：表、列、NOT NULL、唯一约束/CHECK/部分索引是否存在；
2. 行为：该失败的写入确实失败，该放行的写入确实成功（全部在事务内，最后回滚）；
3. 种子：默认场景、默认 Agent、绑定关系、默认知识库；
4. 版本：schema_meta.version 与 backend/app/core/schema.py 一致。

注意：本脚本不会建表/灌种子，也不会改数据；行为用例最后统一 rollback。
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import asyncpg  # noqa: E402

from app.core.schema import SCHEMA_VERSION  # noqa: E402
from app.schemas.scene import (  # noqa: E402
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_SCENE_CODE,
    DEFAULT_SCENE_ID,
)

EXPECTED_TABLES = {
    "scenes",
    "scene_agents",
    "visitors",
    "staff_users",
    "customer_users",
    "sessions",
    "messages",
    "memories",
    "knowledge_bases",
    "documents",
    "agents",
    "tools",
    "audit_logs",
    "tickets",
    "request_traces",
    "canned_scripts",
    "schema_meta",
}

EXPECTED_COLUMNS = {
    "scenes": {
        "id", "code", "display_name", "status", "is_default", "router_mode",
        "router_model", "router_confidence_threshold", "router_max_intents",
        "fallback_agent_id", "fallback_text", "extra_router_prompt",
        "widget_config", "allowed_origins", "publish_key", "api_key_hash", "metadata",
    },
    "agents": {"id", "scene_id", "name", "source", "enabled"},
    "scene_agents": {"scene_id", "agent_id", "enabled", "sort_order"},
    "visitors": {"id", "scene_id", "external_key", "last_seen_at"},
    "sessions": {"id", "scene_id", "user_id", "visitor_id", "last_route_to"},
    "memories": {"id", "user_id", "visitor_id", "memory_uid", "session_id", "source_message_id"},
    "knowledge_bases": {"namespace", "is_active"},
    "canned_scripts": {"id", "scene_id", "key", "category", "priority"},
    "tools": {"id", "scene_id", "name", "type"},
    "request_traces": {
        "id", "scene_code", "visitor_id", "router_skipped", "router_fallback",
        "router_ms", "prompt_hash", "spans",
    },
    "schema_meta": {"key", "value"},
}

EXPECTED_CONSTRAINTS = {
    "uq_agents_scene_name",
    "uq_canned_scripts_scene_key",
    "uq_memories_actor_uid",
    "ck_sessions_actor",
    "ck_memories_actor",
    "ck_agents_code_global",
    "uq_visitors_scene_external",
}

EXPECTED_INDEXES = {
    "uq_scenes_single_default",
    "idx_sessions_scene_created",
    "idx_traces_scene_created",
    "idx_memories_visitor_type",
}


def dsn_from_env() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        from app.core.config import settings

        raw = settings.database_url
    return raw.replace("postgresql+asyncpg://", "postgresql://")


class Checker:
    def __init__(self) -> None:
        self.passed = 0

    def ok(self, label: str) -> None:
        self.passed += 1
        print(f"  [OK] {label}")


async def check_structure(conn: asyncpg.Connection, checker: Checker) -> None:
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    )
    tables = {r["tablename"] for r in rows}
    missing = EXPECTED_TABLES - tables
    assert not missing, f"缺少表: {sorted(missing)}"
    checker.ok(f"表齐全（{len(EXPECTED_TABLES)} 张）")

    rows = await conn.fetch(
        "SELECT table_name, column_name, is_nullable "
        "FROM information_schema.columns WHERE table_schema = 'public'"
    )
    columns: dict[str, set[str]] = {}
    nullability: dict[tuple[str, str], str] = {}
    for r in rows:
        columns.setdefault(r["table_name"], set()).add(r["column_name"])
        nullability[(r["table_name"], r["column_name"])] = r["is_nullable"]
    for table, expected in EXPECTED_COLUMNS.items():
        missing = expected - columns.get(table, set())
        assert not missing, f"{table} 缺少列: {sorted(missing)}"
    checker.ok(f"关键列齐全（{len(EXPECTED_COLUMNS)} 张表）")

    assert nullability[("sessions", "scene_id")] == "NO", "sessions.scene_id 必须是 NOT NULL"
    assert nullability[("sessions", "user_id")] == "YES", "sessions.user_id 应允许 NULL（访客会话）"
    assert nullability[("memories", "user_id")] == "YES", "memories.user_id 应允许 NULL（访客记忆）"
    checker.ok("sessions/memories 的 actor 列可空性正确")

    rows = await conn.fetch(
        "SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace"
    )
    constraints = {r["conname"] for r in rows}
    missing = EXPECTED_CONSTRAINTS - constraints
    assert not missing, f"缺少约束: {sorted(missing)}"
    checker.ok(f"约束齐全（{len(EXPECTED_CONSTRAINTS)} 个）")

    rows = await conn.fetch("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    indexes = {r["indexname"] for r in rows}
    missing = EXPECTED_INDEXES - indexes
    assert not missing, f"缺少索引: {sorted(missing)}"
    checker.ok(f"索引齐全（{len(EXPECTED_INDEXES)} 个）")

    version = await conn.fetchval("SELECT value FROM schema_meta WHERE key = 'version'")
    assert version == SCHEMA_VERSION, f"schema_meta.version 期望 {SCHEMA_VERSION}，实际 {version}"
    checker.ok(f"schema 版本 = {version}")


async def _expect_error(conn, label: str, exc_type, sql: str, *args) -> None:
    try:
        async with conn.transaction():  # savepoint；失败只回滚这一条
            await conn.execute(sql, *args)
    except exc_type:
        return
    raise AssertionError(f"{label}: 预期 {exc_type.__name__}，实际写入成功")


async def check_behaviour(conn: asyncpg.Connection, checker: Checker) -> None:
    suffix = uuid.uuid4().hex[:8]
    code_a = f"__verify_a_{suffix}"
    code_b = f"__verify_b_{suffix}"
    agent_name = f"__verify_agent_{suffix}"
    canned_key = f"__verify_canned_{suffix}"

    tx = conn.transaction()
    await tx.start()
    try:
        scene_a = await conn.fetchval(
            "INSERT INTO scenes (code, display_name) VALUES ($1, $2) RETURNING id",
            code_a, "verify a",
        )
        scene_b = await conn.fetchval(
            "INSERT INTO scenes (code, display_name) VALUES ($1, $2) RETURNING id",
            code_b, "verify b",
        )
        checker.ok("场景可创建")

        await _expect_error(
            conn, "重复 scene.code", asyncpg.UniqueViolationError,
            "INSERT INTO scenes (code, display_name) VALUES ($1, $2)", code_a, "dup",
        )
        checker.ok("重复 scene.code 被拒绝")

        await _expect_error(
            conn, "第二个默认场景", asyncpg.UniqueViolationError,
            "INSERT INTO scenes (code, display_name, is_default) VALUES ($1, $2, TRUE)",
            f"__verify_default_{suffix}", "dup default",
        )
        checker.ok("第二个 is_default 被拒绝")

        await conn.execute(
            "INSERT INTO agents (scene_id, name, source) VALUES ($1, $2, 'dynamic')",
            scene_a, agent_name,
        )
        await _expect_error(
            conn, "同场景同名 Agent", asyncpg.UniqueViolationError,
            "INSERT INTO agents (scene_id, name, source) VALUES ($1, $2, 'dynamic')",
            scene_a, agent_name,
        )
        checker.ok("同场景同名 Agent 被拒绝")
        await conn.execute(
            "INSERT INTO agents (scene_id, name, source) VALUES ($1, $2, 'dynamic')",
            scene_b, agent_name,
        )
        checker.ok("不同场景同名 Agent 放行")

        await _expect_error(
            conn, "code Agent 挂场景", asyncpg.CheckViolationError,
            "INSERT INTO agents (scene_id, name, source) VALUES ($1, $2, 'code')",
            scene_a, f"__verify_code_{suffix}",
        )
        checker.ok("code Agent 不能挂场景")

        await conn.execute(
            "INSERT INTO canned_scripts (scene_id, key) VALUES (NULL, $1)", canned_key
        )
        await conn.execute(
            "INSERT INTO canned_scripts (scene_id, key) VALUES ($1, $2)", scene_a, canned_key
        )
        checker.ok("全局 + 场景同 key 话术可共存")
        await _expect_error(
            conn, "同场景重复话术 key", asyncpg.UniqueViolationError,
            "INSERT INTO canned_scripts (scene_id, key) VALUES ($1, $2)",
            scene_a, canned_key,
        )
        checker.ok("同场景重复话术 key 被拒绝")

        # 访客 external_key 可空：同一场景允许两个 NULL（NULLS DISTINCT）
        await conn.execute("INSERT INTO visitors (scene_id, external_key) VALUES ($1, NULL)", scene_a)
        await conn.execute("INSERT INTO visitors (scene_id, external_key) VALUES ($1, NULL)", scene_a)
        checker.ok("同场景多个 NULL external_key 放行")

        await _expect_error(
            conn, "会话双空 actor", asyncpg.CheckViolationError,
            "INSERT INTO sessions (scene_id) VALUES ($1)", scene_a,
        )
        checker.ok("会话必须有 user 或 visitor")

        customer_id = await conn.fetchval(
            "INSERT INTO customer_users (username, password_hash) VALUES ($1, 'x') RETURNING id",
            f"__verify_user_{suffix}",
        )
        visitor_id = await conn.fetchval(
            "INSERT INTO visitors (scene_id) VALUES ($1) RETURNING id", scene_a
        )
        await _expect_error(
            conn, "会话双 actor", asyncpg.CheckViolationError,
            "INSERT INTO sessions (scene_id, user_id, visitor_id) VALUES ($1, $2, $3)",
            scene_a, customer_id, visitor_id,
        )
        checker.ok("会话不能同时有 user 和 visitor")

        await _expect_error(
            conn, "记忆双空 actor", asyncpg.CheckViolationError,
            "INSERT INTO memories (memory_uid, type, content) VALUES ($1, 'episodic', 'x')",
            f"__verify_mem_{suffix}",
        )
        checker.ok("记忆必须有 user 或 visitor")

        await _expect_error(
            conn, "记忆双 actor", asyncpg.CheckViolationError,
            "INSERT INTO memories (user_id, visitor_id, memory_uid, type, content) "
            "VALUES ($1, $2, $3, 'episodic', 'x')",
            customer_id, visitor_id, f"__verify_mem2_{suffix}",
        )
        checker.ok("记忆不能同时有 user 和 visitor")

    finally:
        await tx.rollback()


async def check_seed(conn: asyncpg.Connection, checker: Checker) -> None:
    defaults = await conn.fetch(
        "SELECT id, code, router_mode FROM scenes WHERE is_default"
    )
    assert len(defaults) == 1, f"默认场景应恰好 1 个，实际 {len(defaults)}"
    default = defaults[0]
    assert str(default["id"]) == DEFAULT_SCENE_ID, "默认场景 UUID 与常量不一致"
    assert default["code"] == DEFAULT_SCENE_CODE, "默认场景 code 与常量不一致"
    assert default["router_mode"] == "direct", "默认场景必须是 direct 模式"
    checker.ok("默认场景唯一且为直连")

    assistant = await conn.fetchrow(
        "SELECT id, enabled FROM agents WHERE name = $1 AND scene_id IS NULL",
        DEFAULT_ASSISTANT_NAME,
    )
    assert assistant is not None, "缺少 default_assistant 代码 Agent"
    assert assistant["enabled"] is True, "default_assistant 被停用"
    checker.ok("默认 Agent 存在且启用")

    count = await conn.fetchval(
        "SELECT count(*) FROM scene_agents WHERE scene_id = $1", uuid.UUID(DEFAULT_SCENE_ID)
    )
    assert count == 1, f"默认场景应恰好绑定 1 个 Agent，实际 {count}"
    checker.ok("默认场景绑定唯一 Agent")

    kb = await conn.fetchval("SELECT 1 FROM knowledge_bases WHERE namespace = 'cs_default'")
    assert kb == 1, "缺少默认知识库 cs_default"
    checker.ok("默认知识库存在")


async def main() -> None:
    conn = await asyncpg.connect(dsn_from_env(), statement_cache_size=0)
    checker = Checker()
    try:
        print("== schema 结构 ==")
        await check_structure(conn, checker)
        print("== 约束行为 ==")
        await check_behaviour(conn, checker)
        print("== 种子 ==")
        await check_seed(conn, checker)
    finally:
        await conn.close()
    print(f"\n✅ schema 校验通过：{checker.passed} 项")


if __name__ == "__main__":
    asyncio.run(main())
