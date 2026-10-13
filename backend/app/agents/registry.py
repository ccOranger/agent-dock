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
