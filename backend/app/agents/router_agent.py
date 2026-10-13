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
from app.agents.registry import AGENT_REGISTRY
from app.core.config import settings

logger = logging.getLogger(__name__)


class RouterAgent:
    def __init__(self):
        self.agent = SimpleAgent(
            name="路由分发器",
            llm=HelloAgentsLLM(model=settings.router_model),
            system_prompt=self._build_prompt(),
            tool_registry=None,  # 不加载任何工具
        )

    def _build_prompt(self) -> str:
        agent_list = "\n".join(
            f"- {name}: {cls.description}" for name, cls in AGENT_REGISTRY.items()
        )
        return ROUTER_SYSTEM_PROMPT.format(agent_list=agent_list)

    async def route(self, query: str) -> dict:
        # SimpleAgent.run 为同步阻塞调用，放入线程池避免阻塞事件循环
        raw = await asyncio.to_thread(self.agent.run, query)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"路由结果解析失败: {raw!r}")
            return {"agent_name": "knowledge_qa", "confidence": 0.3, "reason": "解析失败"}
        return data
