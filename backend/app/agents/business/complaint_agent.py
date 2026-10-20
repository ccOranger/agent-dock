from hello_agents.tools import ToolRegistry

from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.prompts.complaint import COMPLAINT_SYSTEM_PROMPT
from app.tools.custom.ticket_tool import CreateTicketTool, EscalateTool, UpdateTicketTool
from app.tools.memory_factory import get_memory_tool


class ComplaintAgent(BaseBusinessAgent):
    name = "complaint_handler"
    description = "投诉处理：受理客户投诉、安抚情绪、创建工单、升级人工"
    system_prompt = COMPLAINT_SYSTEM_PROMPT
    rag_namespace = "cs_after_sales"

    # 路由线索（供路由 Agent 判定）
    routing_keywords = [
        "投诉", "不满", "差评", "虐待", "态度差", "太慢了", "欺骗",
        "赔偿", "敷衍", "找领导", "曝光", "12315", "监管",
    ]
    routing_examples = [
        "我要投诉，快递太慢了",
        "你们这服务太差了，必须给我个说法",
        "再不处理我就去消费者协会投诉",
    ]

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(CreateTicketTool())
        registry.register_tool(UpdateTicketTool())
        registry.register_tool(EscalateTool())
