"""种子数据：初始用户 + MCP 工具配置示例

用法（在 backend 目录，配好 .env 后）：
    python ../scripts/seed_data.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("PYTHONPATH", "backend")

from app.core.database import engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.entities import (  # noqa: E402
    AgentMeta,
    Base,
    CannedScript,
    CustomerUser,
    Document,
    StaffUser,
    ToolMeta,
)
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

# 固定话术的后台显示名（规则内容以 app.agents.canned_scripts.SEED_RULES 为准，只在建库时写入表）
CANNED_TITLES = {
    "identity": "身份介绍（你是谁）",
    "capability": "能力与服务范围（你能做什么）",
    "probing": "探听系统配置的拒答",
}


# 后台人员（管理后台 admin-frontend 登录）
SEED_STAFF = [
    {"username": "admin", "password": "Admin@123", "role": "admin", "display_name": "管理员"},
    {"username": "service01", "password": "Agent@123", "role": "agent", "display_name": "客服一号"},
]

# 客户（客户端 frontend 登录 / 注册）
SEED_CUSTOMERS = [
    {"username": "alice", "password": "Alice@123", "display_name": "Alice"},
]

# ---- 本地 MCP 服务（FastMCP, stdio）：存相对路径+留空解释器，由加载时按运行环境解析 ----


def _mcp_stdio_config(script_name: str) -> dict:
    """生成跨环境可移植的 stdio 传输配置（方案 A）。

    不再把开发机的绝对解释器/脚本路径写死进 DB：
    - command 留空 → 由 mcp_loader 在加载时用运行进程的 sys.executable；
    - args 存相对 backend 根的路径（mcp_servers/xxx.py）→ 加载时按 backend 根重定位。
    这样同一份 seed 在 Windows/Linux/容器下都能拉起（旧库绝对路径也会被兼容回退）。
    """
    return {
        "transport": "stdio",
        "command": "",
        "args": [f"mcp_servers/{script_name}"],
        "timeout_seconds": 20,
    }


SEED_TOOLS = [
    {
        "name": "mcp_logistics",
        "type": "mcp",
        "schema_": {},
        "config": _mcp_stdio_config("logistics_server.py"),
    },
    {
        "name": "mcp_refund",
        "type": "mcp",
        "schema_": {},
        "config": _mcp_stdio_config("refund_server.py"),
    },
    {
        # 公开免费的第三方 stdio MCP 官方演示服务（需主机已安装 Node/npx，免 API Key）：
        # DB 存跨平台裸命令名 npx，Windows 上由 mcp_loader 自动补 .cmd。
        "name": "mcp_everything",
        "type": "mcp",
        "schema_": {},
        "config": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-everything"],
            "timeout_seconds": 60,
        },
    },
]

# 默认业务 Agent（与 app/agents/registry.py 中的 AGENT_REGISTRY 对齐，source=code）
SEED_AGENTS = [
    {
        "name": "knowledge_qa",
        "display_name": "知识问答",
        "description": "通用知识问答：产品FAQ、公司政策、使用指南",
        "enabled": True,
        "sort_order": 1,
    },
    {
        "name": "order_service",
        "display_name": "订单服务",
        "description": "订单服务：查询订单、物流追踪、修改订单、退换申请",
        "enabled": True,
        "sort_order": 2,
    },
    {
        "name": "complaint_handler",
        "display_name": "投诉处理",
        "description": "投诉处理：受理客户投诉、安抚情绪、创建工单、升级人工",
        "enabled": True,
        "sort_order": 3,
    },
]

# 示例动态 Agent（source=dynamic）：演示后台创建 + MCP 绑定，可在后台继续编辑
SEED_DYNAMIC_AGENTS = [
    {
        "name": "refund_service",
        "display_name": "退款咨询",
        "description": "退款咨询：受理退款申请、查询退款进度（示例动态 Agent，绑定退款 MCP）",
        "enabled": True,
        "sort_order": 10,
        "system_prompt": "你是退款咨询助手，负责解答退款政策与进度问题；必要时调用退款相关工具。",
        "mcp_tool_names": ["mcp_refund"],
    },
]

# 示例知识库文档（仅元数据归档，实际向量存于 Qdrant；命名空间与 data/knowledge_bases 目录对应）
SEED_DOCUMENTS = [
    {
        "title": "产品常见问题 FAQ",
        "source": "docs/product_faq.md",
        "content_type": "text/markdown",
        "status": "ready",
        "rag_namespace": "cs_default",
        "chunk_count": 12,
    },
    {
        "title": "订单与物流自助指南",
        "source": "docs/order_guide.md",
        "content_type": "text/markdown",
        "status": "ready",
        "rag_namespace": "cs_order",
        "chunk_count": 8,
    },
    {
        "title": "售后与投诉处理政策",
        "source": "docs/after_sales_policy.md",
        "content_type": "text/markdown",
        "status": "ready",
        "rag_namespace": "cs_after_sales",
        "chunk_count": 6,
    },
]


async def main() -> None:
    import sys

    from sqlalchemy import select

    # 可选：--rebuild 先删除全部表（用于人员/客户分表后的全量重建，会清空数据）
    rebuild = "--rebuild" in sys.argv
    async with engine.begin() as conn:
        if rebuild:
            await conn.run_sync(Base.metadata.drop_all)
            # 清理遗留的单表 users（分表后不再使用）
            from sqlalchemy import text

            await conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as db:
        for u in SEED_STAFF:
            row = (
                await db.execute(select(StaffUser).where(StaffUser.username == u["username"]))
            ).scalar_one_or_none()
            if row is None:
                db.add(
                    StaffUser(
                        username=u["username"],
                        password_hash=hash_password(u["password"]),
                        role=u["role"],
                        display_name=u["display_name"],
                    )
                )
        for u in SEED_CUSTOMERS:
            row = (
                await db.execute(
                    select(CustomerUser).where(CustomerUser.username == u["username"])
                )
            ).scalar_one_or_none()
            if row is None:
                db.add(
                    CustomerUser(
                        username=u["username"],
                        password_hash=hash_password(u["password"]),
                        display_name=u["display_name"],
                    )
                )
        for t in SEED_TOOLS:
            row = (
                await db.execute(select(ToolMeta).where(ToolMeta.name == t["name"]))
            ).scalar_one_or_none()
            if row is None:
                db.add(ToolMeta(**t))
            else:
                # 覆盖历史遗留的假域名配置（如旧版 sse endpoint），保证本地 stdio 生效
                row.type = t["type"]
                row.schema_ = t["schema_"]
                row.config = t["config"]
        for a in SEED_AGENTS:
            row = (
                await db.execute(select(AgentMeta).where(AgentMeta.name == a["name"]))
            ).scalar_one_or_none()
            if row is None:
                db.add(AgentMeta(source="code", **a))
        for a in SEED_DYNAMIC_AGENTS:
            row = (
                await db.execute(select(AgentMeta).where(AgentMeta.name == a["name"]))
            ).scalar_one_or_none()
            if row is None:
                db.add(AgentMeta(source="dynamic", **a))
        for d in SEED_DOCUMENTS:
            row = (
                await db.execute(
                    select(Document).where(
                        Document.title == d["title"], Document.rag_namespace == d["rag_namespace"]
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                db.add(Document(**d))
        # 固定话术：从种子定义落库（运行时只读表，代码里无兜底）；存过就不覆盖（后台可能改过文案），只补种子标记
        from app.agents.canned_scripts import SEED_RULES

        for rule in SEED_RULES:
            row = (
                await db.execute(select(CannedScript).where(CannedScript.key == rule.key))
            ).scalar_one_or_none()
            if row is None:
                db.add(
                    CannedScript(
                        key=rule.key,
                        title=CANNED_TITLES.get(rule.key, rule.key),
                        category=rule.category,
                        patterns=sorted(rule.patterns),
                        keywords=list(rule.keywords),
                        reply_text=rule.text,
                        priority=rule.priority,
                        enabled=True,
                        is_builtin=True,
                    )
                )
            elif not row.is_builtin:
                row.is_builtin = True
        await db.commit()
    print(
        "✅ seed 完成：2 后台人员(admin/service01) + 1 客户(alice) + 3 MCP 工具(2 本地stdio + 1 npx everything) + 3 代码 Agent + 1 动态 Agent + 3 示例文档 + 3 条固定话术"
    )


if __name__ == "__main__":
    asyncio.run(main())
