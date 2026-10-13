"""
HelloAgents 框架启动初始化
系统启动时执行一次，构建全局共享的 LLM、工具注册中心、默认 Agent 实例
"""
import logging
import os

from hello_agents import HelloAgentsLLM
from hello_agents.agents import SimpleAgent
from hello_agents.tools import MemoryTool, RAGTool, ToolRegistry

from app.core.config import settings

logger = logging.getLogger(__name__)


class HelloAgentsBootstrap:
    """HelloAgents 启动引导器（单例）"""

    def __init__(self):
        self.llm: HelloAgentsLLM | None = None
        self.memory_tool: MemoryTool | None = None
        self.rag_tool: RAGTool | None = None
        self.tool_registry: ToolRegistry | None = None
        self.agent: SimpleAgent | None = None

    def init_all(self) -> None:
        # 1. 初始化工具
        logger.info("1. 初始化工具...")

        # ---- MemoryTool：四层记忆（系统级默认实例）----
        # 记忆层由环境 MEMORY_TYPES 驱动（semantic 需 Neo4j，未部署时默认不启用）
        self.memory_tool = MemoryTool(
            user_id="system_bootstrap_user",
            memory_types=settings.memory_type_list,
        )

        # ---- RAGTool：知识库检索 ----
        self.rag_tool = RAGTool(
            knowledge_base_path=os.getenv("RAG_KB_ROOT", "./data/knowledge_bases"),
            rag_namespace=os.getenv("RAG_DEFAULT_NAMESPACE", "cs_default"),
        )

        logger.info("✅ MemoryTool 和 RAGTool 初始化完成")

        # 2. 注册工具
        logger.info("2. 注册工具...")
        self.tool_registry = ToolRegistry()
        self.tool_registry.register_tool(self.memory_tool)
        self.tool_registry.register_tool(self.rag_tool)
        logger.info("✅ 工具注册完成")

        # 3. 创建默认 Agent
        logger.info("3. 创建 Agent...")
        self.llm = HelloAgentsLLM()
        self.agent = SimpleAgent(
            name="智能客服助手",
            llm=self.llm,
            system_prompt=(
                "你是一名专业的智能客服助手。"
                "请优先基于记忆与知识库回答客户问题；"
                "涉及订单、工单等业务时，调用相应工具；"
                "无法确定时，转人工处理。"
            ),
            tool_registry=self.tool_registry,
        )
        logger.info("✅ Agent 创建完成")

    def shutdown(self) -> None:
        self.agent = None
        self.tool_registry = None
        self.rag_tool = None
        self.memory_tool = None
        self.llm = None


# 全局单例
bootstrap = HelloAgentsBootstrap()


def init_hello_agents() -> HelloAgentsBootstrap:
    """在 FastAPI lifespan 中调用"""
    bootstrap.init_all()
    return bootstrap


async def validate_agents_sync() -> None:
    """启动时校验：代码注册表 与 DB 中 source=code 的行 是否一致。

    动态 Agent（source=dynamic）由后台创建，不属于代码注册表，不参与此校验。
    """
    from app.agents.registry import AGENT_REGISTRY
    from app.models.repositories import AgentRepo

    all_rows = await AgentRepo.list_all()
    code_db_names = {r.name for r in all_rows if r.source == "code"}
    code_names = set(AGENT_REGISTRY.keys())

    if code_db_names - code_names:
        raise RuntimeError(f"DB 中以下 code Agent 未在代码注册: {code_db_names - code_names}")

    for missing in code_names - code_db_names:
        cls = AGENT_REGISTRY[missing]
        await AgentRepo.create(
            name=missing,
            display_name=cls.description[:64] if cls.description else missing,
        )
        logger.info(f"自动登记 Agent: {missing}")
