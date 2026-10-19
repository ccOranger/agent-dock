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

    # 路由线索（供路由 Agent 判定）：通用业务知识与政策类提问，外加“你是谁/你能做什么”这类元问题
    # 与寒暄：兼做全系统的兜底承接 Agent，避免这类提问直接转人工。仅前 12 个关键词会注入提示词，
    # 故按价值排序（元问题在前，与“政策/规则”语义重叠的“条件/说明”、低频的“运费/营业时间”不占位）。
    routing_keywords = [
        "你是谁", "你好", "能做什么", "是什么", "怎么用", "能否", "是否支持",
        "政策", "规则", "FAQ", "指南", "保修",
    ]
    routing_examples = [
        "你是谁，都能帮我做什么",
        "你们支持七天无理由退货吗",
        "这个产品的使用说明在哪里看",
    ]

    def build_tools(self, registry: ToolRegistry) -> None:
        registry.register_tool(get_memory_tool(self.user_id))
        registry.register_tool(get_rag_tool(self.rag_namespace))
