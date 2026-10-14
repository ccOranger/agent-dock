"""
DynamicBusinessAgent：由管理后台在 DB 中配置的动态业务 Agent

与代码注册的 Agent（KnowledgeAgent/OrderAgent/...）不同：
- 定义（system_prompt / 模型 / rag_namespace / MCP 绑定）全部来自 agents 表
- 无进程内自定义 Python 工具，能力边界 = prompt + memory/rag + 所绑定的 MCP 工具
- 按需求默认挂载 memory 工具（与所有代码 Agent 保持一致）
"""
from hello_agents.tools import ToolRegistry

from app.agents.base_business_agent import BaseBusinessAgent
from app.tools.memory_factory import get_memory_tool
from app.tools.rag_factory import get_rag_tool


class DynamicBusinessAgent(BaseBusinessAgent):
    def __init__(self, meta, user_id: str, session_id: str | None = None):
        super().__init__(user_id=user_id, session_id=session_id)
        # meta 为 detached 的 AgentMeta ORM 行（source=dynamic），列均已加载
        self.name = meta.name
        self.description = meta.description or meta.name
        self.system_prompt = meta.system_prompt or ""
        self.llm_model = meta.llm_model or ""
        self.rag_namespace = meta.rag_namespace
        self.search_all_knowledge_bases = bool(meta.search_all_knowledge_bases)
        self.mcp_tools = list(meta.mcp_tool_names or [])

    def build_tools(self, registry: ToolRegistry) -> None:
        # 动态 Agent 必挂 memory 工具
        registry.register_tool(get_memory_tool(self.user_id))
        # 绑定了知识库时挂 RAG 检索工具
        if self.rag_namespace:
            registry.register_tool(get_rag_tool(self.rag_namespace))
