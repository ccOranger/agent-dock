"""重建数据库并写入种子数据（适配远端 DB 代理：单次 simple query 往返完成全部 DDL+DML）

背景：云库 203.0.113.10 位于事务级连接池代理之后，命名 prepared statement 会被回收，
导致 SQLAlchemy/asyncpg 的多次扩展协议往返报 "connection was closed in the middle of operation"。
本脚本把「删除旧表 + 依据 init_db.sql 建表 + 种子 INSERT」拼成一条多语句字符串，
用 asyncpg 的 simple query 协议一次性执行（单个网络往返），规避该问题。

用法（backend 目录）：
    .\\.venv\\Scripts\\python.exe ..\\scripts\\rebuild_and_seed.py

生产环境（APP_ENV=production）默认拒绝执行：升级请用 ``python -m app.migrate``。
确需重建必须显式加 ``--force``，并确认已有可用备份。
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import asyncpg  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.schemas.scene import DEFAULT_ASSISTANT_NAME, DEFAULT_SCENE_ID  # noqa: E402

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
    CANNED_TITLES,
    DEMO_INTEGRATION_DESCRIPTION,
    DEMO_INTEGRATION_ID,
    DEMO_INTEGRATION_NAME,
    DEMO_INTEGRATION_PUBLIC_CODE,
    DEMO_MODE,
    DEMO_SCENE_AGENT_NAMES,
    DEMO_SCENE_CODE,
    DEMO_SCENE_DESCRIPTION,
    DEMO_SCENE_ID,
    DEMO_SCENE_NAME,
    SEED_AGENTS,
    ingest_demo_documents,
)

from app.agents.canned_scripts import SEED_RULES  # noqa: E402
from app.services.canned_templates import TEMPLATE_KEYS  # noqa: E402

# 需要删除的表（含遗留单表 users）；先删子表再删父表，CASCADE 兜底依赖
DROP_TABLES = [
    "chat_events",
    "request_traces",
    "tickets",
    "audit_logs",
    "messages",
    "memories",
    "sessions",
    "documents",
    "knowledge_bases",
    "tools",
    "canned_scripts",
    "scene_agents",
    "agents",
    "integration_scenes",
    "integration_keys",
    "integrations",
    "visitors",
    "scenes",
    "customer_users",
    "staff_users",
    "users",
    "schema_meta",
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
            "system_prompt, routing_keywords, routing_examples, rag_namespace, "
            "mcp_tool_names, custom_tool_names) VALUES "
            f"({lit(a['name'])}, {lit(a['display_name'])}, {lit(a['description'])}, "
            f"{lit(a['enabled'])}, {lit(a['sort_order'])}, 'dynamic', {lit(a['system_prompt'])}, "
            f"{lit(a.get('routing_keywords', []))}, {lit(a.get('routing_examples', []))}, "
            f"{lit(a.get('rag_namespace'))}, "
            f"{lit(a.get('mcp_tool_names', []))}, {lit(a.get('custom_tool_names', []))});"
        )
    # 默认场景只绑定默认助手；放在 Agent 插入之后，子查询才能取到 id
    parts.append(
        "INSERT INTO scene_agents (scene_id, agent_id, enabled, sort_order) "
        f"SELECT {lit(DEFAULT_SCENE_ID)}, id, TRUE, 0 FROM agents "
        f"WHERE name = {lit(DEFAULT_ASSISTANT_NAME)} AND scene_id IS NULL "
        "ON CONFLICT (scene_id, agent_id) DO NOTHING;"
    )
    # 演示场景：客服场景（LLM 路由到投诉 / 知识问答 / 订单三个演示动态 Agent）
    if DEMO_MODE:
        parts.append(
            "INSERT INTO scenes (id, code, display_name, description, router_mode) VALUES ("
            f"{lit(DEMO_SCENE_ID)}, {lit(DEMO_SCENE_CODE)}, {lit(DEMO_SCENE_NAME)}, "
            f"{lit(DEMO_SCENE_DESCRIPTION)}, 'llm');"
        )
        for order, agent_name in enumerate(DEMO_SCENE_AGENT_NAMES):
            parts.append(
                "INSERT INTO scene_agents (scene_id, agent_id, enabled, sort_order) "
                f"SELECT {lit(DEMO_SCENE_ID)}, id, TRUE, {order} FROM agents "
                f"WHERE name = {lit(agent_name)} AND scene_id IS NULL "
                "ON CONFLICT (scene_id, agent_id) DO NOTHING;"
            )
        # 单租户降级：默认接入方（Key 容器）授权全部场景，Key 调用任意场景都不会 403
        parts.append(
            "INSERT INTO integrations (id, name, public_code, description) VALUES ("
            f"{lit(DEMO_INTEGRATION_ID)}, {lit(DEMO_INTEGRATION_NAME)}, "
            f"{lit(DEMO_INTEGRATION_PUBLIC_CODE)}, {lit(DEMO_INTEGRATION_DESCRIPTION)});"
        )
        parts.append(
            "INSERT INTO integration_scenes (integration_id, scene_id) "
            f"SELECT {lit(DEMO_INTEGRATION_ID)}, id FROM scenes "
            "ON CONFLICT (integration_id, scene_id) DO NOTHING;"
        )
    # 固定话术：全局行（scene_id NULL）。重建后与 seed_data.py 的种子保持一致。
    for rule in SEED_RULES:
        parts.append(
            "INSERT INTO canned_scripts "
            "(scene_id, key, title, category, patterns, keywords, reply_text, priority, enabled, is_builtin) "
            "VALUES (NULL, "
            f"{lit(rule.key)}, {lit(CANNED_TITLES.get(rule.key, rule.key))}, {lit(rule.category)}, "
            f"{lit(sorted(rule.patterns))}, {lit(list(rule.keywords))}, {lit(rule.text)}, "
            f"{lit(rule.priority)}, TRUE, TRUE);"
        )
    # 场景身份/能力话术：按全局模板复制一份到每个场景（与 seed_data.py、
    # 后台新建场景走同一套规则：只补缺失 key，不覆盖运营改过的行）。
    parts.append(
        "INSERT INTO canned_scripts "
        "(scene_id, key, title, category, patterns, keywords, reply_text, "
        "answer_source, kb_namespaces, priority, enabled, is_builtin) "
        "SELECT s.id, c.key, c.title, c.category, c.patterns, c.keywords, c.reply_text, "
        "c.answer_source, c.kb_namespaces, c.priority, TRUE, FALSE "
        "FROM scenes s CROSS JOIN canned_scripts c "
        f"WHERE c.scene_id IS NULL AND c.key IN ({', '.join(lit(k) for k in TEMPLATE_KEYS)}) "
        "AND NOT EXISTS (SELECT 1 FROM canned_scripts x "
        "WHERE x.scene_id = s.id AND x.key = c.key) "
        "ON CONFLICT (scene_id, key) DO NOTHING;"
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


def _refuse_in_production(force: bool) -> None:
    """生产库误跑全量重建会直接删光业务数据，必须显式放行。"""
    env = (settings.app_env or "").strip().lower()
    if force or env not in {"production", "prod"}:
        return
    raise SystemExit(
        "检测到 APP_ENV=production：已拒绝全量重建（会 DROP 全部业务表）。\n"
        "升级请用增量迁移：python -m app.migrate\n"
        "确需重建请追加 --force，并确认已有可用备份。"
    )


async def main(force: bool = False) -> None:
    _refuse_in_production(force)
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    sql = build_sql()
    conn = await asyncpg.connect(url, statement_cache_size=0)
    try:
        # 单次 simple query 往返执行全部语句（DDL + 种子 INSERT）
        await conn.execute(sql)
        print("[OK] 重建 + seed 语句已提交")
        if DEMO_MODE:
            print("   演示账号：admin/Admin@123、service01/Agent@123、alice/Alice@123")
            print(
                f"   默认接入方：{DEMO_INTEGRATION_NAME}（{DEMO_INTEGRATION_PUBLIC_CODE}），"
                "已授权全部场景；嵌入代码无需传 data-integration"
            )
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
                "(select count(*) from scenes) scenes, "
                "(select count(*) from scene_agents) scene_agents, "
                "(select count(*) from integrations) integrations, "
                "(select count(*) from integration_scenes) integration_scenes, "
                "(select count(*) from visitors) visitors, "
                "(select count(*) from request_traces) traces, "
                "(select count(*) from chat_events) chat_events"
            )
            print(
                f"   行数 -> staff={row['staff']} customer={row['cust']} "
                f"agents={row['agents']} tools={row['tools']} "
                f"documents={row['docs']} tickets={row['tickets']} "
                f"scenes={row['scenes']} scene_agents={row['scene_agents']} "
                f"integrations={row['integrations']} "
                f"integration_scenes={row['integration_scenes']} "
                f"visitors={row['visitors']} traces={row['traces']} "
                f"chat_events={row['chat_events']}"
            )
        finally:
            await vconn.close()
    except Exception as e:  # noqa: BLE001
        print("   （校验查询失败，可忽略/稍后手动核对）:", type(e).__name__, e)

    # 演示文档：重建只落归档行，原文 → 向量在这里补（Qdrant/embedding 不可用时只提示）
    if DEMO_MODE:
        await ingest_demo_documents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="全量重建数据库并写入种子（仅开发/演示）")
    parser.add_argument(
        "--force", action="store_true", help="生产环境（APP_ENV=production）下也允许重建（危险）"
    )
    asyncio.run(main(force=parser.parse_args().force))
