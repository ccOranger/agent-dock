from hello_agents.tools import ToolRegistry

from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.prompts.it_incident import IT_INCIDENT_SYSTEM_PROMPT
from app.tools.custom.it_ticket_tool import CreateItTicketTool, EscalateItTool, UpdateItTicketTool
from app.tools.memory_factory import get_memory_tool


class ITIncidentAgent(BaseBusinessAgent):
    name = "it_incident"
    description = "IT 故障受理：设备/网络/账号/系统故障报修、建单、补充信息与升级人工"
    system_prompt = IT_INCIDENT_SYSTEM_PROMPT
    rag_namespace = "it_default"

    # 路由线索（供路由 Agent 判定）：故障现象与报修诉求；仅前 12 个关键词会注入提示词。
    routing_keywords = [
        "故障", "报修", "坏了", "蓝屏", "死机", "开不了机", "断网", "无法上网",
        "系统崩溃", "报错", "卡顿", "中毒",
    ]
    routing_examples = [
        "我的笔记本开不了机，一直黑屏",
        "办公室突然断网了，整层都上不了网",
        "电脑频繁蓝屏报错，麻烦帮我报修",
        "好像中了勒索病毒，文件都打不开了",
    ]

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(CreateItTicketTool())
        registry.register_tool(UpdateItTicketTool())
        registry.register_tool(EscalateItTool())
