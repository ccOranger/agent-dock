from hello_agents.tools import ToolRegistry

from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.prompts.knowledge import KNOWLEDGE_SYSTEM_PROMPT
from app.tools.memory_factory import get_memory_tool
from app.tools.rag_factory import get_rag_tool


class KnowledgeAgent(BaseBusinessAgent):
    name = "knowledge_qa"
    description = "通用知识问答：产品FAQ、公司政策、使用指南"
    system_prompt = KNOWLEDGE_SYSTEM_PROMPT
    rag_namespace = "cs_default"

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(get_rag_tool(self.rag_namespace))
