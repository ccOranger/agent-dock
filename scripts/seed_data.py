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
    CustomerUser,
    Document,
    StaffUser,
    ToolMeta,
)
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402


# 后台人员（管理后台 admin-frontend 登录）
SEED_STAFF = [
    {"username": "admin", "password": "Admin@123", "role": "admin", "display_name": "管理员"},
    {"username": "service01", "password": "Agent@123", "role": "agent", "display_name": "客服一号"},
]

# 客户（客户端 frontend 登录 / 注册）
SEED_CUSTOMERS = [
    {"username": "alice", "password": "Alice@123", "display_name": "Alice"},
]

SEED_TOOLS = [
    {
        "name": "mcp_logistics",
        "type": "mcp",
        "schema_": {},
        "config": {"endpoint": "http://mcp-logistics:9000/sse", "transport": "sse", "timeout_seconds": 15},
    },
    {
        "name": "mcp_refund",
        "type": "mcp",
        "schema_": {},
        "config": {"endpoint": "http://mcp-refund:9001/sse", "transport": "sse", "timeout_seconds": 15},
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
        await db.commit()
    print(
        "✅ seed 完成：2 后台人员(admin/service01) + 1 客户(alice) + 2 MCP 工具 + 3 代码 Agent + 1 动态 Agent + 3 示例文档"
    )


if __name__ == "__main__":
    asyncio.run(main())
