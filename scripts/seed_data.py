"""种子数据：初始用户 + MCP 工具 + 默认场景 + 固定话术

只写种子，不建表：换库请先执行 scripts/init_db.py（或 rebuild_and_seed.py）。

用法（在 backend 目录，配好 .env 后）：
    python ../scripts/init_db.py
    python ../scripts/seed_data.py
"""
import asyncio
import os
import secrets
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("PYTHONPATH", "backend")

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.database import engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.entities import (  # noqa: E402
    AgentMeta,
    CannedScript,
    CustomerUser,
    Document,
    Scene,
    SceneAgent,
    StaffUser,
    Ticket,
    ToolMeta,
)
from app.schemas.scene import (  # noqa: E402
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_SCENE_CODE,
    DEFAULT_SCENE_ID,
    DEFAULT_SCENE_NAME,
)

# ===== 种子模式 =====
# 默认关闭演示数据：开源/生产部署不应带固定口令账号。
DEMO_MODE = os.getenv("SEED_DEMO", "").strip().lower() in {"1", "true", "yes", "on"}

ADMIN_USERNAME = os.getenv("SEED_ADMIN_USERNAME", "admin").strip() or "admin"
_ADMIN_PASSWORD_ENV = os.getenv("SEED_ADMIN_PASSWORD", "").strip()
if DEMO_MODE:
    ADMIN_PASSWORD = ""
    ADMIN_PASSWORD_GENERATED = False
elif _ADMIN_PASSWORD_ENV:
    ADMIN_PASSWORD = _ADMIN_PASSWORD_ENV
    ADMIN_PASSWORD_GENERATED = False
else:
    ADMIN_PASSWORD = secrets.token_urlsafe(12)
    ADMIN_PASSWORD_GENERATED = True

# 固定话术的后台显示名（规则内容以 app.agents.canned_scripts.SEED_RULES 为准，只在建库时写入表）
CANNED_TITLES = {
    "identity": "身份介绍（你是谁）",
    "capability": "能力与服务范围（你能做什么）",
    "probing": "探听系统配置的拒答",
}


# 演示后台人员（仅 SEED_DEMO=true 时写入）
DEMO_STAFF = [
    {"username": "admin", "password": "Admin@123", "role": "admin", "display_name": "管理员"},
    {"username": "service01", "password": "Agent@123", "role": "agent", "display_name": "客服一号"},
]

# 演示客户（仅 SEED_DEMO=true 时写入）
DEMO_CUSTOMERS = [
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
]

# 演示工具：依赖 npx，离线环境不可用，仅 SEED_DEMO=true 时写入
DEMO_TOOLS = [
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

# 代码侧只保留内核默认助手（与 app/agents/registry.py 的 AGENT_REGISTRY 对齐，source=code）；
# 业务 Agent 一律由后台动态创建（agents 表 source=dynamic），不再种代码业务线
SEED_AGENTS = [
    # 默认场景绑定的内核级 Agent：中立问答，只读默认知识库
    {
        "name": DEFAULT_ASSISTANT_NAME,
        "display_name": "默认助手",
        "description": "平台默认问答助手：只依据默认知识库回答，不承诺订单/退款等业务能力",
        "enabled": True,
        "sort_order": 0,
    },
]

# 示例动态 Agent（source=dynamic，仅演示模式写入）：演示后台创建 + MCP 绑定
DEMO_DYNAMIC_AGENTS = [
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

# 示例知识库文档（仅演示模式写入；实际向量存于 Qdrant）
DEMO_DOCUMENTS = [
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
    # IT 服务台示例知识（与 IT 知识库命名空间 it_default/it_network/it_assets 对齐）
    {
        "title": "IT 服务台自助指南",
        "source": "docs/it_helpdesk_guide.md",
        "content_type": "text/markdown",
        "status": "ready",
        "rag_namespace": "it_default",
        "chunk_count": 10,
    },
    {
        "title": "办公网络与 VPN 故障排查手册",
        "source": "docs/it_network_guide.md",
        "content_type": "text/markdown",
        "status": "ready",
        "rag_namespace": "it_network",
        "chunk_count": 9,
    },
    {
        "title": "IT 资产领用与保修说明",
        "source": "docs/it_asset_guide.md",
        "content_type": "text/markdown",
        "status": "ready",
        "rag_namespace": "it_assets",
        "chunk_count": 7,
    },
]

# 示例 IT 工单（仅演示模式写入）：覆盖 open/in_progress/escalated/resolved 四种状态
DEMO_TICKETS = [
    {
        "ticket_no": "TK-IT0001",
        "category": "it_network",
        "urgency": "high",
        "status": "open",
        "title": "财务部 5F 无法连接 Wi-Fi",
        "content": "财务部 5F 整层笔记本无法连接 Wi-Fi，有线网络正常，开始时间 09:20，影响约 12 人。",
        "updates": [],
        "metadata_": {"source": "it_helpdesk"},
    },
    {
        "ticket_no": "TK-IT0002",
        "category": "it_hardware",
        "urgency": "medium",
        "status": "in_progress",
        "title": "笔记本开机黑屏并伴随风扇异响",
        "content": "资产编号 IT-LT-1002 的 ThinkPad 开机黑屏，风扇持续高速运转，已尝试长按电源键重启无效。",
        "updates": [
            {"content": "已远程确认电源与内存状态，建议送修检测主板。", "at": "2026-10-19T10:30:00+08:00"}
        ],
        "metadata_": {"source": "it_helpdesk"},
    },
    {
        "ticket_no": "TK-IT0003",
        "category": "it_account",
        "urgency": "high",
        "status": "escalated",
        "title": "邮箱账号异地登录疑似被盗",
        "content": "收到异地登录提醒，发件箱出现未知邮件，疑似账号被盗，已升级安全事件处理。",
        "updates": [
            {"content": "升级 IT 人工：账号被盗需立即冻结并重置密码。", "at": "2026-10-19T14:05:00+08:00"}
        ],
        "metadata_": {"source": "it_helpdesk"},
    },
    {
        "ticket_no": "TK-IT0004",
        "category": "it_software",
        "urgency": "low",
        "status": "resolved",
        "title": "Office 激活失败提示密钥无效",
        "content": "Office 365 激活失败，提示产品密钥无效；已通过重新登录企业账号完成激活。",
        "updates": [
            {"content": "已指导用户重新登录企业账号并完成激活。", "at": "2026-10-18T16:40:00+08:00"}
        ],
        "metadata_": {"source": "it_helpdesk"},
    },
]


ACTIVE_STAFF = (
    DEMO_STAFF
    if DEMO_MODE
    else [
        {
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
            "role": "admin",
            "display_name": "管理员",
        }
    ]
)
ACTIVE_CUSTOMERS = DEMO_CUSTOMERS if DEMO_MODE else []
ACTIVE_TOOLS = SEED_TOOLS + DEMO_TOOLS if DEMO_MODE else SEED_TOOLS
ACTIVE_DYNAMIC_AGENTS = DEMO_DYNAMIC_AGENTS if DEMO_MODE else []
ACTIVE_DOCUMENTS = DEMO_DOCUMENTS if DEMO_MODE else []
ACTIVE_TICKETS = DEMO_TICKETS if DEMO_MODE else []


async def main() -> None:
    from sqlalchemy import delete, select, update

    if "--rebuild" in sys.argv:
        raise SystemExit("seed_data.py 不再建表/删表；全量重建请使用 scripts/rebuild_and_seed.py")

    async with AsyncSession(engine) as db:
        # 默认场景：init_db.sql 已写入；这里兜底老库/只跑 seed 的环境
        scene = await db.get(Scene, uuid.UUID(DEFAULT_SCENE_ID))
        if scene is None:
            scene = Scene(
                id=uuid.UUID(DEFAULT_SCENE_ID),
                code=DEFAULT_SCENE_CODE,
                display_name=DEFAULT_SCENE_NAME,
                description="无 scene_code 时的默认落点；单 Agent 直连，只读",
                is_default=True,
                router_mode="direct",
            )
            db.add(scene)
            await db.flush()

        created_staff: list[str] = []
        for u in ACTIVE_STAFF:
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
                created_staff.append(u["username"])
        for u in ACTIVE_CUSTOMERS:
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
        for t in ACTIVE_TOOLS:
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
                await db.execute(
                    select(AgentMeta).where(
                        AgentMeta.name == a["name"], AgentMeta.scene_id.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                db.add(AgentMeta(source="code", scene_id=None, **a))

        # 已下线的代码业务 Agent：旧库里 source=code 的行连同场景绑定一起清理
        # （scene_agents 显式删，不依赖库上的 ON DELETE CASCADE）；
        # 场景兜底若指向它们，一并解除，避免路由档案持续报「兜底不在池内」
        seed_code_names = {a["name"] for a in SEED_AGENTS}
        orphan_rows = (
            await db.execute(
                select(AgentMeta).where(
                    AgentMeta.source == "code",
                    AgentMeta.name.notin_(seed_code_names),
                )
            )
        ).scalars().all()
        if orphan_rows:
            orphan_ids = [row.id for row in orphan_rows]
            await db.execute(delete(SceneAgent).where(SceneAgent.agent_id.in_(orphan_ids)))
            await db.execute(
                update(Scene)
                .where(Scene.fallback_agent_id.in_(orphan_ids))
                .values(fallback_agent_id=None)
            )
            for row in orphan_rows:
                await db.delete(row)

        for a in ACTIVE_DYNAMIC_AGENTS:
            row = (
                await db.execute(
                    select(AgentMeta).where(
                        AgentMeta.name == a["name"], AgentMeta.scene_id.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                db.add(AgentMeta(source="dynamic", scene_id=None, **a))
        for d in ACTIVE_DOCUMENTS:
            row = (
                await db.execute(
                    select(Document).where(
                        Document.title == d["title"], Document.rag_namespace == d["rag_namespace"]
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                db.add(Document(**d))
        for t in ACTIVE_TICKETS:
            row = (
                await db.execute(select(Ticket).where(Ticket.ticket_no == t["ticket_no"]))
            ).scalar_one_or_none()
            if row is None:
                db.add(Ticket(**t))
        # 固定话术：从种子定义落库（运行时只读表，代码里无兜底）；存过就不覆盖（后台可能改过文案），只补种子标记
        from app.agents.canned_scripts import SEED_RULES

        for rule in SEED_RULES:
            row = (
                await db.execute(
                    select(CannedScript).where(
                        CannedScript.key == rule.key, CannedScript.scene_id.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                db.add(
                    CannedScript(
                        scene_id=None,
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

        # 默认场景只绑定默认助手；绑定关系幂等，已存在则保留后台的启停状态
        default_agent = (
            await db.execute(
                select(AgentMeta).where(
                    AgentMeta.name == DEFAULT_ASSISTANT_NAME, AgentMeta.scene_id.is_(None)
                )
            )
        ).scalar_one_or_none()
        if default_agent is not None:
            link = await db.get(SceneAgent, (scene.id, default_agent.id))
            if link is None:
                db.add(
                    SceneAgent(
                        scene_id=scene.id,
                        agent_id=default_agent.id,
                        enabled=True,
                        sort_order=0,
                    )
                )
        await db.commit()
    demo_tag = "（演示模式）" if DEMO_MODE else ""
    print(
        f"✅ seed 完成{demo_tag}：{len(ACTIVE_STAFF)} 后台人员 + "
        f"{len(ACTIVE_CUSTOMERS)} 客户 + {len(ACTIVE_TOOLS)} 工具 + "
        f"{len(SEED_AGENTS)} 代码 Agent + {len(ACTIVE_DYNAMIC_AGENTS)} 动态 Agent + "
        f"{len(ACTIVE_DOCUMENTS)} 示例文档 + {len(ACTIVE_TICKETS)} 示例工单 + {len(SEED_RULES)} 条固定话术"
        f" + 默认场景 {DEFAULT_SCENE_CODE}（直连）"
    )
    if DEMO_MODE:
        print("   演示账号：admin/Admin@123、service01/Agent@123、alice/Alice@123")
    elif ADMIN_USERNAME in created_staff:
        print("=" * 64)
        print(f"已创建管理员：{ADMIN_USERNAME}")
        if ADMIN_PASSWORD_GENERATED:
            print(f"初始密码（仅显示一次）：{ADMIN_PASSWORD}")
        print("请登录后立即修改密码；也可用 SEED_ADMIN_PASSWORD 指定初始密码。")
        print("=" * 64)
    else:
        print(f"   管理员 {ADMIN_USERNAME} 已存在，未重置密码")


if __name__ == "__main__":
    asyncio.run(main())
