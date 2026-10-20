from hello_agents.tools import ToolRegistry

from app.agents.base_business_agent import BaseBusinessAgent
from app.packs.it_helpdesk.prompts.asset import IT_ASSET_SYSTEM_PROMPT
from app.packs.it_helpdesk.tools.asset_tool import QueryItAssetTool
from app.tools.memory_factory import get_memory_tool


class ITAssetAgent(BaseBusinessAgent):
    name = "it_asset"
    description = "IT 资产查询：设备型号、序列号、领用人、保修期与网络配置"
    system_prompt = IT_ASSET_SYSTEM_PROMPT
    rag_namespace = "it_assets"

    # 路由线索（供路由 Agent 判定）：资产台账类查询；仅前 12 个关键词会注入提示词。
    routing_keywords = [
        "资产", "设备", "电脑", "显示器", "序列号", "SN", "保修", "领用",
        "IP", "MAC", "编号", "型号",
    ]
    routing_examples = [
        "帮我查一下资产编号 IT-LT-1001 的信息",
        "张伟名下有哪些设备",
        "这台笔记本的保修期到什么时候",
    ]

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(QueryItAssetTool())
