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
    # 为 True 时，知识库检索覆盖全部启用中的知识库（而非仅 rag_namespace），
    # 用于通用问答 Agent，确保管理后台新建的知识库无需改代码即可被命中。
    search_all_knowledge_bases: bool = False
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
        mcp_tool_names: list[str] = []
        if self.mcp_tools:
            from app.tools.mcp_loader import register_mcp_tools

            mcp_tool_names = register_mcp_tools(registry, self.mcp_tools, mcp_configs or {})

        # 3. 创建 SimpleAgent（默认模型来自环境配置 LLM_MODEL_ID）
        model = self.llm_model or settings.llm_model_id
        return SimpleAgent(
            name=self.name,
            llm=HelloAgentsLLM(model=model),
            system_prompt=self._final_system_prompt(mcp_tool_names),
            tool_registry=registry,
        )

    def _final_system_prompt(self, mcp_tool_names: list[str]) -> str:
        """当确实带来了可用 MCP 工具时，附加“必须调用工具”约束。

        SimpleAgent 用文本 ReAct（[TOOL_CALL:...]）而非原生 function-calling，且工具清单
        只展示名称+描述、不展示参数名。若提示词未明确要求，模型很容易声称“无法获取
        实时数据”而直接编造答案。因此只要 MCP 工具注册成功，就在系统提示里强制要求
        先调用工具再作答。
        """
        prompt = self.system_prompt or ""
        if not mcp_tool_names:
            return prompt
        tools_line = "、".join(mcp_tool_names)
        return (
            f"{prompt}\n\n## 工具使用要求（必须遵守）\n"
            f"你可以使用以下实时工具：{tools_line}。\n"
            "1. 当用户问题需要实时外部数据（天气、地点/坐标、路线、距离、POI 搜索等）时，\n"
            "   必须先从上述工具中选择最合适的一个发起调用，拿到结果后再整理作答。\n"
            "2. 调用格式：[TOOL_CALL:工具名:参数名=参数值]，它必须是回复的第一个字符，\n"
            "   之前不得输出任何中英文说明或过渡语；参数名参考下方工具描述。\n"
            "3. 严禁自行编造、模拟或续写工具返回结果（包括 <result>、JSON 数据等）；\n"
            "   发出调用后立即停止输出，等待系统返回真实结果。\n"
            "4. 严禁在未调用工具的情况下声称“无法获取实时信息”，也严禁凭训练知识编造数据。"
        )
