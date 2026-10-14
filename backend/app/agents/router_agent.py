"""
RouterAgent：路由 Agent
- 独立于所有业务 Agent
- 只做意图识别与分发决策
- 不加载任何业务工具
"""
import asyncio
import json
import logging

from hello_agents import HelloAgentsLLM
from hello_agents.agents import SimpleAgent

from app.agents.prompts.router import ROUTER_SYSTEM_PROMPT
from app.agents.registry import AGENT_REGISTRY, list_effective_agents
from app.core.config import settings

logger = logging.getLogger(__name__)


class RouterAgent:
    def __init__(self):
        self.llm = HelloAgentsLLM(model=settings.router_model)
        # 默认提示词（仅代码注册表）：保证无 DB 时仍可工作，也供测试直接读取
        self.agent = SimpleAgent(
            name="路由分发器",
            llm=self.llm,
            system_prompt=self._build_prompt(),
            tool_registry=None,  # 不加载任何工具
        )

    @staticmethod
    def _format_agent_list(agents: list[dict]) -> str:
        return "\n".join(f"- {a['name']}: {a['description']}" for a in agents)

    def _build_prompt(self) -> str:
        """仅基于代码注册表构建（兼容旧测试；动态列表请用 build_prompt_async）。"""
        agent_list = "\n".join(
            f"- {name}: {cls.description}" for name, cls in AGENT_REGISTRY.items()
        )
        return ROUTER_SYSTEM_PROMPT.format(agent_list=agent_list)

    async def build_prompt_async(self) -> str:
        """基于当前生效的 Agent（代码 + 启用中的动态）实时生成路由提示词。"""
        agents = await list_effective_agents()
        return ROUTER_SYSTEM_PROMPT.format(agent_list=self._format_agent_list(agents))

    async def route(self, query: str) -> dict:
        # 每次路由实时拼入动态 Agent 清单，使后台新增的 Agent 无需改代码即可被路由到
        system_prompt = await self.build_prompt_async()
        agent = SimpleAgent(
            name="路由分发器",
            llm=self.llm,
            system_prompt=system_prompt,
            tool_registry=None,
        )
        # SimpleAgent.run 为同步阻塞调用，放入线程池避免阻塞事件循环
        raw = await asyncio.to_thread(agent.run, query)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"路由结果解析失败: {raw!r}")
            return {"agent_name": "knowledge_qa", "confidence": 0.3, "reason": "解析失败"}
        return data
