"""
Orchestrator：调度器
  1. 调用 RouterAgent 识别意图
  2. 从注册表取出业务 Agent 类
  3. 实例化业务 Agent
  4. 构建 SimpleAgent 并流式执行（含工具调用循环）
"""
import asyncio
import logging
import threading
from typing import AsyncIterator, Iterator

from hello_agents import Message
from hello_agents.agents import SimpleAgent

from app.agents.registry import resolve_business_agent
from app.agents.router_agent import RouterAgent
from app.core.config import settings
from app.core.context_engineer import ContextEngineer

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = settings.router_confidence_threshold

_SENTINEL = object()

# SimpleAgent 的文本 ReAct 标记前缀
_TOOL_MARKER = "[TOOL_CALL:"


def _safe_prefix_len(text: str) -> int | None:
    """计算可以安全下发给前端的前缀长度。

    返回 None 表示本轮已出现完整工具调用标记（本轮文本一律不下发）；
    返回整数表示可下发 [0:n)，末尾若是标记的不完整前缀（如 "[TOOL"）则暂缓到判定清楚。
    """
    if _TOOL_MARKER in text:
        return None
    bracket = text.rfind("[")
    if bracket != -1 and _TOOL_MARKER.startswith(text[bracket:]):
        return bracket
    return len(text)


def _stream_with_tools(
    agent: SimpleAgent, input_text: str, max_tool_iterations: int = 3
) -> Iterator[dict]:
    """流式执行 + 真正的工具调用循环（同步生成器，产出 dict 事件）。

    hello_agents 的 stream_run 只做一次 LLM 流式调用、完全没有工具循环，
    模型会把 [TOOL_CALL:...] 和 <result>{...}</result> 一起“演”出来（假数据）。
    这里对齐 SimpleAgent.run 的语义自行实现循环，并区分下发内容：
    - 中间轮（含工具调用）的原始文本不下发，只发 tool_call / tool_result 事件；
    - 最终轮文本仍逐块流式下发（无工具调用时 UX 不变）。
    """
    messages: list[dict] = [
        {"role": "system", "content": agent._get_enhanced_system_prompt()}
    ]
    messages.extend({"role": m.role, "content": m.content} for m in agent._history)
    messages.append({"role": "user", "content": input_text})

    tool_mode = bool(getattr(agent, "enable_tool_calling", False)) and agent.tool_registry is not None

    if not tool_mode:
        full = ""
        for chunk in agent.llm.stream_invoke(messages):
            full += chunk
            yield {"type": "text", "data": {"content": chunk}}
        agent.add_message(Message(input_text, "user"))
        agent.add_message(Message(full, "assistant"))
        return

    iteration = 0
    final_text = ""
    while iteration < max_tool_iterations:
        buffered = ""
        sent = 0
        suppress = False
        for chunk in agent.llm.stream_invoke(messages):
            buffered += chunk
            if suppress:
                continue
            safe = _safe_prefix_len(buffered)
            if safe is None:
                suppress = True
                continue
            if safe > sent:
                yield {"type": "text", "data": {"content": buffered[sent:safe]}}
                sent = safe

        tool_calls = agent._parse_tool_calls(buffered) if buffered else []
        if not tool_calls:
            if not suppress and len(buffered) > sent:
                yield {"type": "text", "data": {"content": buffered[sent:]}}
            final_text = buffered
            break

        clean = buffered
        results: list[str] = []
        for idx, call in enumerate(tool_calls):
            call_id = f"call-{iteration}-{idx}"
            tool_name = call["tool_name"]
            raw_args = call["parameters"]
            try:
                args = agent._parse_tool_parameters(tool_name, raw_args)
            except Exception:  # noqa: BLE001
                args = {"raw": raw_args}
            yield {"type": "tool_call", "data": {"id": call_id, "name": tool_name, "args": args}}

            result = agent._execute_tool_call(tool_name, raw_args)
            results.append(result)
            clean = clean.replace(call["original"], "")
            yield {
                "type": "tool_result",
                "data": {"id": call_id, "name": tool_name, "result": result, "error": None},
            }

        messages.append({"role": "assistant", "content": clean.strip()})
        messages.append(
            {
                "role": "user",
                "content": "工具执行结果：\n" + "\n\n".join(results) + "\n\n请基于这些结果给出完整的回答。",
            }
        )
        iteration += 1

    if iteration >= max_tool_iterations and not final_text:
        final_text = agent.llm.invoke(messages)
        yield {"type": "text", "data": {"content": final_text}}

    agent.add_message(Message(input_text, "user"))
    agent.add_message(Message(final_text, "assistant"))


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
                for event in _stream_with_tools(simple_agent, query):
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

        # ---- 3~4. 解析业务 Agent（代码注册表优先，其次 DB 动态 Agent）并实例化 ----
        business_agent = await resolve_business_agent(
            agent_name, user_id=user_id, session_id=session_id
        )
        if business_agent is None:
            logger.error(f"未注册的 Agent: {agent_name}")
            yield {"type": "error", "data": {"code": 5006, "message": "Agent 未注册"}}
            return

        # ---- 4.1 在主 event loop 上预取 MCP 配置（避免 build() 跨循环访问 DB）----
        mcp_configs: dict[str, dict | None] = {}
        if business_agent.mcp_tools:
            from app.tools.mcp_loader import fetch_mcp_configs

            mcp_configs = await fetch_mcp_configs(business_agent.mcp_tools)

        # ---- 5. 上下文工程 ----
        rag_namespaces = await self._resolve_rag_namespaces(business_agent)
        enriched_query = await self.ctx_engineer.build(
            user_id=user_id,
            query=query,
            rag_namespaces=rag_namespaces,
        )

        # ---- 6. 构建 SimpleAgent 并流式执行（stream_run 为同步生成器，桥接为异步流）----
        # build() 内部会构造 MCPTool，而 MCPTool.__init__ 的 _discover_tools() 是
        # 无超时的阻塞网络调用；必须放到工作线程，否则会冻住事件循环导致全部接口挂起。
        # （MCP 配置已在上面于主循环预取，build 不再触碰 DB，可安全跨线程）
        simple_agent = await asyncio.to_thread(business_agent.build, mcp_configs)
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

    @staticmethod
    async def _resolve_rag_namespaces(business_agent) -> list[str]:
        """解析业务 Agent 实际检索的知识库命名空间列表。

        - search_all_knowledge_bases=True：覆盖所有启用中的知识库（含自身默认空间），
          使管理后台新建的知识库无需改代码即可被命中。
        - 否则：仅使用 Agent 自身声明的 rag_namespace。
        """
        own = business_agent.rag_namespace
        if getattr(business_agent, "search_all_knowledge_bases", False):
            from app.models.repositories import KnowledgeBaseRepo

            bases = await KnowledgeBaseRepo.list_all()
            namespaces = [b.namespace for b in bases if b.is_active]
            if own:
                namespaces.insert(0, own)
            seen: set[str] = set()
            ordered: list[str] = []
            for ns in namespaces:
                if ns and ns not in seen:
                    seen.add(ns)
                    ordered.append(ns)
            return ordered
        return [own] if own else []
