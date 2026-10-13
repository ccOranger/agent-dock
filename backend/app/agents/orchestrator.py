"""
Orchestrator：调度器
  1. 调用 RouterAgent 识别意图
  2. 从注册表取出业务 Agent 类
  3. 实例化业务 Agent
  4. 构建 SimpleAgent 并流式执行
"""
import asyncio
import logging
import threading
from typing import AsyncIterator

from hello_agents.agents import SimpleAgent

from app.agents.registry import get_agent_class
from app.agents.router_agent import RouterAgent
from app.core.config import settings
from app.core.context_engineer import ContextEngineer

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = settings.router_confidence_threshold

_SENTINEL = object()


class Orchestrator:
    def __init__(self):
        self.router = RouterAgent()
        self.ctx_engineer = ContextEngineer()

    @staticmethod
    async def _astream(simple_agent: SimpleAgent, query: str) -> AsyncIterator[dict]:
        """将 SimpleAgent.stream_run（同步生成器）桥接为异步流。

        后台线程逐条产出事件，通过线程安全的方式回投到事件循环的队列，
        避免阻塞事件循环同时保留逐个流式送达的语义。
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _pump() -> None:
            try:
                for event in simple_agent.stream_run(query):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as exc:  # noqa: BLE001
                logger.exception("stream_run 执行异常")
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "data": {"code": 5001, "message": str(exc)}},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        threading.Thread(target=_pump, daemon=True).start()

        while True:
            event = await queue.get()
            if event is _SENTINEL:
                break
            # hello_agents SimpleAgent.stream_run 产出的是纯文本块，
            # 统一归一化为 dict 事件，保证下游 dispatch()/SSE 处理契约一致。
            if isinstance(event, str):
                yield {"type": "text", "data": {"content": event}}
            else:
                yield event

    async def dispatch(
        self,
        user_id: str,
        session_id: str,
        query: str,
        history: list[dict],
    ) -> AsyncIterator[dict]:
        # ---- 1. 路由 ----
        intent = await self.router.route(query)
        agent_name = intent.get("agent_name", "knowledge_qa")
        confidence = float(intent.get("confidence", 0.0))
        logger.info(f"[route] {agent_name} conf={confidence} reason={intent.get('reason')}")

        yield {
            "type": "route",
            "data": {
                "agent_name": agent_name,
                "confidence": confidence,
                "reason": intent.get("reason", ""),
            },
        }

        # ---- 2. 低置信度 → 转人工 ----
        if confidence < CONFIDENCE_THRESHOLD:
            yield {
                "type": "handover",
                "data": {"reason": "low_confidence", "confidence": confidence},
            }
            return

        # ---- 3. 取业务 Agent 类 ----
        agent_cls = get_agent_class(agent_name)
        if agent_cls is None:
            logger.error(f"未注册的 Agent: {agent_name}")
            yield {"type": "error", "data": {"code": 5006, "message": "Agent 未注册"}}
            return

        # ---- 4. 实例化业务 Agent ----
        business_agent = agent_cls(user_id=user_id, session_id=session_id)

        # ---- 4.1 在主 event loop 上预取 MCP 配置（避免 build() 跨循环访问 DB）----
        mcp_configs: dict[str, dict | None] = {}
        if business_agent.mcp_tools:
            from app.tools.mcp_loader import fetch_mcp_configs

            mcp_configs = await fetch_mcp_configs(business_agent.mcp_tools)

        # ---- 5. 上下文工程 ----
        enriched_query = await self.ctx_engineer.build(
            user_id=user_id,
            query=query,
            rag_namespace=business_agent.rag_namespace,
        )

        # ---- 6. 构建 SimpleAgent 并流式执行（stream_run 为同步生成器，桥接为异步流）----
        simple_agent = business_agent.build(mcp_configs)
        async for event in self._astream(simple_agent, enriched_query):
            yield event

        # ---- 7. 异步归档记忆 ----
        await self.ctx_engineer.persist_after_run(
            user_id=user_id,
            session_id=session_id,
            query=query,
            agent_name=agent_name,
            history=history,
        )
