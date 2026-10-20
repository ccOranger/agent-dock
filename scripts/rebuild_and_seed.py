"""重建数据库并写入种子数据（适配远端 DB 代理：单次 simple query 往返完成全部 DDL+DML）

背景：云库 203.0.113.10 位于事务级连接池代理之后，命名 prepared statement 会被回收，
导致 SQLAlchemy/asyncpg 的多次扩展协议往返报 "connection was closed in the middle of operation"。
本脚本把「删除旧表 + 依据 init_db.sql 建表 + 种子 INSERT」拼成一条多语句字符串，
用 asyncpg 的 simple query 协议一次性执行（单个网络往返），规避该问题。

用法（backend 目录）：
    .\\.venv\\Scripts\\python.exe ..\\scripts\\rebuild_and_seed.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import asyncpg  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402

# 复用 seed_data.py 的种子定义，避免重复维护
sys.path.insert(0, os.path.dirname(__file__))
from seed_data import (  # noqa: E402
    ACTIVE_CUSTOMERS,
    ACTIVE_DOCUMENTS,
    ACTIVE_DYNAMIC_AGENTS,
    ACTIVE_STAFF,
    ACTIVE_TICKETS,
    ACTIVE_TOOLS,
    ADMIN_PASSWORD,
    ADMIN_PASSWORD_GENERATED,
    ADMIN_USERNAME,
    DEMO_MODE,
    SEED_AGENTS,
)

# 需要删除的表（含遗留单表 users）；先删子表再删父表由 CASCADE 兜底
DROP_TABLES = [
    "request_traces",
    "tickets",
    "audit_logs",
    "messages",
    "sessions",
    "memories",
    "documents",
    "knowledge_bases",
    "tools",
    "agents",
    "customer_users",
    "staff_users",
    "users",
]


def lit(v) -> str:
    """把 Python 值转成可安全内联的 SQL 字面量（simple 协议无参数绑定）。"""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False)
    else:
        s = str(v)
    return "'" + s.replace("'", "''") + "'"


def build_sql() -> str:
    parts: list[str] = []

    # 1) 删除旧表
    for t in DROP_TABLES:
        parts.append(f"DROP TABLE IF EXISTS {t} CASCADE;")

    # 2) 建表：直接复用 init_db.sql（其中 CREATE EXTENSION IF NOT EXISTS 为幂等）
    init_path = os.path.join(os.path.dirname(__file__), "init_db.sql")
    with open(init_path, encoding="utf-8") as f:
        parts.append(f.read())

    # 3) 种子数据
    for u in ACTIVE_STAFF:
        parts.append(
            "INSERT INTO staff_users (username, password_hash, role, display_name) VALUES "
            f"({lit(u['username'])}, {lit(hash_password(u['password']))}, {lit(u['role'])}, "
            f"{lit(u['display_name'])});"
        )
    for u in ACTIVE_CUSTOMERS:
        parts.append(
            "INSERT INTO customer_users (username, password_hash, display_name) VALUES "
            f"({lit(u['username'])}, {lit(hash_password(u['password']))}, {lit(u['display_name'])});"
        )
    for t in ACTIVE_TOOLS:
        parts.append(
            "INSERT INTO tools (name, type, schema, config) VALUES "
            f"({lit(t['name'])}, {lit(t['type'])}, {lit(t['schema_'])}, {lit(t['config'])});"
        )
    for a in SEED_AGENTS:
        parts.append(
            "INSERT INTO agents (name, display_name, description, enabled, sort_order, source) VALUES "
            f"({lit(a['name'])}, {lit(a['display_name'])}, {lit(a['description'])}, "
            f"{lit(a['enabled'])}, {lit(a['sort_order'])}, 'code');"
        )
    for a in ACTIVE_DYNAMIC_AGENTS:
        parts.append(
            "INSERT INTO agents (name, display_name, description, enabled, sort_order, source, "
            "system_prompt, mcp_tool_names) VALUES "
            f"({lit(a['name'])}, {lit(a['display_name'])}, {lit(a['description'])}, "
            f"{lit(a['enabled'])}, {lit(a['sort_order'])}, 'dynamic', {lit(a['system_prompt'])}, "
            f"{lit(a['mcp_tool_names'])});"
        )
    for d in ACTIVE_DOCUMENTS:
        parts.append(
            "INSERT INTO documents (title, source, content_type, status, rag_namespace, chunk_count, kind) VALUES "
            f"({lit(d['title'])}, {lit(d['source'])}, {lit(d['content_type'])}, {lit(d['status'])}, "
            f"{lit(d['rag_namespace'])}, {lit(d['chunk_count'])}, 'upload');"
        )
    for t in ACTIVE_TICKETS:
        parts.append(
            "INSERT INTO tickets (ticket_no, category, urgency, status, title, content, updates, metadata) VALUES "
            f"({lit(t['ticket_no'])}, {lit(t['category'])}, {lit(t['urgency'])}, {lit(t['status'])}, "
            f"{lit(t['title'])}, {lit(t['content'])}, {lit(t['updates'])}, {lit(t['metadata_'])});"
        )

    return "\n".join(parts)


async def main() -> None:
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    sql = build_sql()
    conn = await asyncpg.connect(url, statement_cache_size=0)
    try:
        # 单次 simple query 往返执行全部语句（DDL + 种子 INSERT）
        await conn.execute(sql)
        print("[OK] 重建 + seed 语句已提交")
        if DEMO_MODE:
            print("   演示账号：admin/Admin@123、service01/Agent@123、alice/Alice@123")
        else:
            print(f"   管理员账号：{ADMIN_USERNAME}")
            if ADMIN_PASSWORD_GENERATED:
                print(f"   初始密码（仅显示一次）：{ADMIN_PASSWORD}")
            print("   请登录后立即修改密码。")
    finally:
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass

    # 校验另起一条连接，且失败不影响上面已成功的重建
    try:
        vconn = await asyncpg.connect(url, statement_cache_size=0)
        try:
            row = await vconn.fetchrow(
                "select "
                "(select count(*) from staff_users) staff, "
                "(select count(*) from customer_users) cust, "
                "(select count(*) from agents) agents, "
                "(select count(*) from tools) tools, "
                "(select count(*) from documents) docs, "
                "(select count(*) from tickets) tickets, "
                "(select count(*) from request_traces) traces"
            )
            print(
                f"   行数 -> staff={row['staff']} customer={row['cust']} "
                f"agents={row['agents']} tools={row['tools']} "
                f"documents={row['docs']} tickets={row['tickets']} traces={row['traces']}"
            )
        finally:
            await vconn.close()
    except Exception as e:  # noqa: BLE001
        print("   （校验查询失败，可忽略/稍后手动核对）:", type(e).__name__, e)


if __name__ == "__main__":
    asyncio.run(main())
