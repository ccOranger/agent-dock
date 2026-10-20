from hello_agents.tools import ToolRegistry

from app.agents.base_business_agent import BaseBusinessAgent
from app.packs.it_helpdesk.prompts.knowledge import IT_KNOWLEDGE_SYSTEM_PROMPT
from app.tools.memory_factory import get_memory_tool
from app.tools.rag_factory import get_rag_tool


class ITKnowledgeAgent(BaseBusinessAgent):
    name = "it_knowledge"
    description = "IT 知识问答：账号、VPN、邮箱、打印机、办公软件与安全规范"
    system_prompt = IT_KNOWLEDGE_SYSTEM_PROMPT
    # 默认检索 IT 通用库，同时覆盖网络与资产两个专项库（运维手册/设备使用说明）
    rag_namespace = "it_default"
    rag_namespaces = ["it_network", "it_assets"]

    # 路由线索（供路由 Agent 判定）：知识/操作类提问；仅前 12 个关键词会注入提示词。
    # 与故障 Agent 区分：这里承接“怎么用/怎么申请”，故障类“坏了/断网”交给 it_incident。
    routing_keywords = [
        "VPN", "账号", "密码", "邮箱", "登录", "网络", "打印机", "软件",
        "权限", "安全", "安装", "申请",
    ]
    routing_examples = [
        "VPN 怎么连接，总是掉线怎么办",
        "邮箱密码忘了怎么重置",
        "公司电脑可以安装哪些软件",
        "共享盘权限怎么申请",
    ]

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(get_rag_tool(self.rag_namespace))
