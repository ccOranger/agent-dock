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
    # 通用问答需覆盖管理后台新建的所有知识库（如 cs_pgsql）
    search_all_knowledge_bases = True

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(get_rag_tool(self.rag_namespace))
