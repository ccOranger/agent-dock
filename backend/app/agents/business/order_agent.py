from hello_agents.tools import ToolRegistry

from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.prompts.order import ORDER_SYSTEM_PROMPT
from app.tools.custom.logistics_tool import QueryLogisticsTool
from app.tools.custom.order_tool import ListOrdersTool, ModifyOrderTool, QueryOrderTool
from app.tools.memory_factory import get_memory_tool


class OrderAgent(BaseBusinessAgent):
    name = "order_service"
    description = "订单服务：查询订单、物流追踪、修改订单、退换申请"
    system_prompt = ORDER_SYSTEM_PROMPT
    rag_namespace = "cs_order"

    # 本业务使用的 MCP 工具
    mcp_tools = ["mcp_logistics", "mcp_refund"]

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(ListOrdersTool())
        registry.register_tool(QueryOrderTool())
        registry.register_tool(ModifyOrderTool())
        registry.register_tool(QueryLogisticsTool())
