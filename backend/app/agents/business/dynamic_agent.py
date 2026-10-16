"""
DynamicBusinessAgent：由管理后台在 DB 中配置的动态业务 Agent

与代码注册的 Agent（KnowledgeAgent/OrderAgent/...）不同：
- 定义（system_prompt / 模型 / rag_namespace / MCP 绑定）全部来自 agents 表
- 无进程内自定义 Python 工具，能力边界 = prompt + memory/rag + 所绑定的 MCP 工具
- 按需求默认挂载 memory 工具（与所有代码 Agent 保持一致）
"""
from hello_agents.tools import ToolRegistry

from app.agents.base_business_agent import BaseBusinessAgent
from app.tools.custom_loader import register_custom_tools
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
        self.rag_namespaces = list(meta.rag_namespaces or [])
        self.search_all_knowledge_bases = bool(meta.search_all_knowledge_bases)
        self.mcp_tools = list(meta.mcp_tool_names or [])
        self.custom_tool_names = list(meta.custom_tool_names or [])

    def build_tools(self, registry: ToolRegistry) -> None:
        # 动态 Agent 必挂 memory 工具
        registry.register_tool(get_memory_tool(self.user_id))
        # 模型可调用的 RAG 工具挂主库（自身默认库或显式多库首个）；
        # 多库跨空检索上下文由 ContextEngineer 按解析后的 rag_namespaces 自动注入。
        primary = self.rag_namespace or (self.rag_namespaces[0] if self.rag_namespaces else None)
        if primary:
            registry.register_tool(get_rag_tool(primary))
        # 代码自定义工具：按名从代码目录实例化并注册（会话上下文参数由执行时自动注入）。
        self.registered_custom_tools = register_custom_tools(registry, self.custom_tool_names)
