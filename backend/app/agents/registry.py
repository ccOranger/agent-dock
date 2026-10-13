"""
AgentRegistry：业务 Agent 类注册表
每个业务 Agent 是一个类，独立开发，注册到这里即可被路由分发
"""
from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.business.complaint_agent import ComplaintAgent
from app.agents.business.knowledge_agent import KnowledgeAgent
from app.agents.business.order_agent import OrderAgent
from app.core.config import settings

# 新增业务 Agent：只需 import 并加入此表
AGENT_REGISTRY: dict[str, type[BaseBusinessAgent]] = {
    KnowledgeAgent.name: KnowledgeAgent,
    OrderAgent.name: OrderAgent,
    ComplaintAgent.name: ComplaintAgent,
    # RefundAgent.name: RefundAgent,
}


def get_agent_class(name: str) -> type[BaseBusinessAgent] | None:
    return AGENT_REGISTRY.get(name)


def list_agents() -> list[dict]:
    return [
        {
            "name": cls.name,
            "description": cls.description,
            "model": cls.llm_model or settings.llm_model_id,
            "rag_namespace": cls.rag_namespace,
            "mcp_tools": cls.mcp_tools,
        }
        for cls in AGENT_REGISTRY.values()
    ]


# ===== 动态 Agent（DB 驱动）支持 =====
async def list_dynamic_agents(enabled_only: bool = True) -> list[dict]:
    """返回 DB 中 source=dynamic 的 Agent 摘要（供路由与后台展示）。"""
    from app.models.repositories import AgentRepo

    rows = await AgentRepo.list_dynamic(enabled_only=enabled_only)
    return [
        {
            "name": r.name,
            "description": r.description or r.name,
            "model": r.llm_model or settings.llm_model_id,
            "rag_namespace": r.rag_namespace,
            "mcp_tools": list(r.mcp_tool_names or []),
        }
        for r in rows
    ]


async def list_effective_agents() -> list[dict]:
    """代码注册表 + 启用中的动态 Agent 合并清单（路由分发器据此识别意图）。"""
    return list_agents() + await list_dynamic_agents(enabled_only=True)


async def resolve_business_agent(name: str, user_id: str, session_id: str | None = None):
    """按名称解析出业务 Agent 实例：优先代码注册表，其次 DB 动态 Agent。

    返回 BaseBusinessAgent 实例（代码或动态）；未注册返回 None。
    """
    cls = get_agent_class(name)
    if cls is not None:
        return cls(user_id=user_id, session_id=session_id)

    from app.agents.business.dynamic_agent import DynamicBusinessAgent
    from app.models.repositories import AgentRepo

    meta = await AgentRepo.get_by_name(name)
    if meta is not None and meta.source == "dynamic" and meta.enabled:
        return DynamicBusinessAgent(meta=meta, user_id=user_id, session_id=session_id)
    return None
