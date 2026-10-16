from hello_agents.tools import ToolRegistry

from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.prompts.complaint import COMPLAINT_SYSTEM_PROMPT
from app.tools.custom.ticket_tool import CreateTicketTool, UpdateTicketTool, EscalateTool
from app.tools.memory_factory import get_memory_tool


class ComplaintAgent(BaseBusinessAgent):
    name = "complaint_handler"
    description = "投诉处理：受理客户投诉、安抚情绪、创建工单、升级人工"
    system_prompt = COMPLAINT_SYSTEM_PROMPT
    rag_namespace = "cs_after_sales"

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(CreateTicketTool())
        registry.register_tool(UpdateTicketTool())
        registry.register_tool(EscalateTool())
