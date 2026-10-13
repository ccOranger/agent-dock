"""
BaseBusinessAgent：所有业务 Agent 的抽象基类
"""
from abc import ABC, abstractmethod

from hello_agents import HelloAgentsLLM
from hello_agents.agents import SimpleAgent
from hello_agents.tools import ToolRegistry

from app.core.config import settings


class BaseBusinessAgent(ABC):
    # ---- 子类必须声明的类属性 ----
    name: str = ""
    description: str = ""
    system_prompt: str = ""
    # 为空则使用环境配置的默认模型（LLM_MODEL_ID）；子类可按需覆盖
    llm_model: str = ""
    max_iterations: int = 5
    rag_namespace: str | None = None
    mcp_tools: list[str] = []

    def __init__(self, user_id: str, session_id: str | None = None):
        self.user_id = user_id
        self.session_id = session_id

    @abstractmethod
    def build_tools(self, registry: ToolRegistry) -> None:
        """每个业务 Agent 独立实现自己的工具装配"""
        ...

    def build(self, mcp_configs: dict[str, dict | None] | None = None) -> SimpleAgent:
        registry = ToolRegistry()

        # 1. 业务专属工具
        self.build_tools(registry)

        # 2. MCP 工具（配置由调用方在主 event loop 上预取，避免跨循环访问 DB）
        if self.mcp_tools:
            from app.tools.mcp_loader import register_mcp_tools

            register_mcp_tools(registry, self.mcp_tools, mcp_configs or {})

        # 3. 创建 SimpleAgent（默认模型来自环境配置 LLM_MODEL_ID）
        model = self.llm_model or settings.llm_model_id
        return SimpleAgent(
            name=self.name,
            llm=HelloAgentsLLM(model=model),
            system_prompt=self.system_prompt,
            tool_registry=registry,
        )
