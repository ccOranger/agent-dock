"""
Orchestrator：调度器
  1. 调用 RouterAgent 识别意图（可多意图）
  2. 从注册表取出业务 Agent 类
  3. 实例化业务 Agent
  4. 构建 SimpleAgent 并流式执行（含工具调用循环）；多意图时按主次串行执行并用
     agent_start / agent_end 事件分块下发
"""
import asyncio
import logging
import threading
from typing import AsyncIterator, Iterator

from hello_agents import Message
from hello_agents.agents import SimpleAgent

from app.agents.registry import AGENT_REGISTRY, resolve_business_agent
from app.agents.router_agent import RouterAgent
from app.core.config import settings
from app.core.context_engineer import ContextEngineer
from app.core.tracing import (
    OUTCOME_ANSWERED,
    OUTCOME_ERROR,
    OUTCOME_HANDOVER,
    SPAN_BUILD,
    SPAN_RESOLVE,
    SPAN_RUN,
)

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = settings.router_confidence_threshold

_SENTINEL = object()

# SimpleAgent 的文本 ReAct 标记前缀
_TOOL_MARKER = "[TOOL_CALL:"

# 多意图分块之间插入的分隔（markdown 水平线，前端逐字渲染即可分段）
_INTENT_SEPARATOR = "\n\n---\n\n"


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
        last_route_to: str | None = None,
        max_intents: int | None = None,
        collector=None,
    ) -> AsyncIterator[dict]:
        """一轮对话的完整调度。

        - history / last_route_to 一并交给路由，使追问能续接上一轮的 Agent
        - max_intents：本轮最多执行的意图数，默认取 settings.router_max_intents
        - collector：链路采集器（app.core.tracing.TraceCollector），为空则不采集
        """
        max_intents = max(1, int(max_intents if max_intents is not None else settings.router_max_intents))

        # ---- 1. 路由（可多意图） ----
        intent_result = await self.router.route(
            query, history=history, last_route_to=last_route_to, collector=collector
        )
        intents: list[dict] = intent_result["intents"]
        if collector:
            collector.record_intents(intents, max_intents)
        primary = intents[0]
        agent_name = primary["agent_name"]
        confidence = primary["confidence"]
        logger.info(
            f"[route] {agent_name} intents={len(intents)} conf={confidence} reason={primary['reason']}"
        )

        yield {
            "type": "route",
            "data": {
                # 保留旧契约：agent_name/confidence/reason 均为主意图
                "agent_name": agent_name,
                "confidence": confidence,
                "reason": primary["reason"],
                "intents": intents[:max_intents],
                "intent_total": min(len(intents), max_intents),
            },
        }

        # ---- 2. 低置信度 → 转人工（主意图不达标则整轮转人工；
        #          仅次要意图不达标则丢弃该分支、主意图照常应答）----
        qualified = [i for i in intents if i["confidence"] >= CONFIDENCE_THRESHOLD]
        if not qualified:
            if collector:
                collector.record_outcome(OUTCOME_HANDOVER, reason="low_confidence")
                collector.mark_finished()
            yield {
                "type": "handover",
                "data": {"reason": "low_confidence", "confidence": confidence},
            }
            return

        targets = qualified[:max_intents]

        # ---- 3~6. 逐意图串行：解析 Agent → 上下文工程 → 流式执行，分块下发 ----
        streamed = False
        handled: list[str] = []
        for intent in targets:
            intent_name = intent["agent_name"]
            # 代码注册表优先，其次 DB 动态 Agent
            business_agent = await resolve_business_agent(
                intent_name, user_id=user_id, session_id=session_id
            )
            if business_agent is None:
                logger.error(f"未注册的 Agent: {intent_name}")
                if collector:
                    collector.add(
                        SPAN_RESOLVE, intent_name, status="error", error="agent_not_registered"
                    )
                # 多意图时跳过该分支，其余意图照常应答；全部不可用才在循环后上报
                continue

            index = len(handled)
            if collector:
                source = "code" if intent_name in AGENT_REGISTRY else "dynamic"
                collector.add_unique("agent_names", intent_name)
                collector.add_unique("agent_sources", source)
                collector.add(
                    SPAN_RESOLVE,
                    intent_name,
                    agent_source=source,
                    intent_index=index,
                    declared_mcp=list(getattr(business_agent, "mcp_tools", []) or []),
                    rag_namespace=getattr(business_agent, "rag_namespace", None),
                )

            if streamed:
                # 第二个及以后意图前插入分隔，避开两段回答粘连
                yield {"type": "text", "data": {"content": _INTENT_SEPARATOR}}

            yield {
                "type": "agent_start",
                "data": {
                    "agent_name": intent_name,
                    "intent_index": index,
                    "intent_total": len(targets),
                    "confidence": intent["confidence"],
                    "reason": intent["reason"],
                },
            }

            run_key = collector.start(SPAN_RUN, intent_name, intent_index=index) if collector else ""
            produced = False
            text_chars = 0
            open_tools: dict[str, str] = {}
            async for event in self._run_business_agent(
                business_agent, user_id, query, collector=collector, intent_index=index
            ):
                etype = event.get("type")
                data = event.get("data") or {}
                if collector and etype == "tool_call":
                    open_tools[data.get("id")] = collector.record_tool_call(
                        name=data.get("name", ""),
                        args=data.get("args"),
                        agent_name=intent_name,
                        iteration=index,
                    )
                elif collector and etype == "tool_result":
                    collector.finish_tool_call(
                        open_tools.pop(data.get("id"), ""),
                        result=data.get("result"),
                        error=data.get("error"),
                    )
                elif etype == "text":
                    text_chars += len(data.get("content") or "")
                    if collector:
                        collector.mark_first_token()
                produced = True
                yield event

            if collector:
                collector.end(
                    run_key,
                    status="ok" if produced else "empty",
                    intent_index=index,
                    response_chars=text_chars,
                )

            yield {
                "type": "agent_end",
                "data": {
                    "agent_name": intent_name,
                    "intent_index": index,
                    "intent_total": len(targets),
                },
            }
            handled.append(intent_name)
            streamed = streamed or produced

        if not streamed:
            if collector:
                collector.record_outcome(OUTCOME_ERROR, code=5006, message="Agent 未注册")
                collector.mark_finished()
            yield {"type": "error", "data": {"code": 5006, "message": "Agent 未注册"}}
            return

        if collector:
            collector.record_outcome(OUTCOME_ANSWERED)

        # ---- 7. 异步归档记忆（一轮只归档一次，记在主意图名下） ----
        await self.ctx_engineer.persist_after_run(
            user_id=user_id,
            session_id=session_id,
            query=query,
            agent_name=agent_name,
            history=history,
            collector=collector,
        )

    async def _run_business_agent(
        self,
        business_agent,
        user_id: str,
        query: str,
        collector=None,
        intent_index: int = 0,
    ) -> AsyncIterator[dict]:
        """构建并流式执行单个业务 Agent（含 MCP 预取与上下文工程）。"""
        agent_name = getattr(business_agent, "name", "?")
        # ---- 在主 event loop 上预取 MCP 配置（避免 build() 跨循环访问 DB）----
        mcp_configs: dict[str, dict | None] = {}
        if business_agent.mcp_tools:
            from app.tools.mcp_loader import fetch_mcp_configs

            mcp_configs = await fetch_mcp_configs(business_agent.mcp_tools)
            if collector:
                for name in business_agent.mcp_tools:
                    collector.add_unique("mcp_server_names", name)

        # ---- 上下文工程 ----
        rag_namespaces = await self._resolve_rag_namespaces(business_agent)
        enriched_query = await self.ctx_engineer.build(
            user_id=user_id,
            query=query,
            rag_namespaces=rag_namespaces,
            collector=collector,
        )

        # ---- 构建 SimpleAgent 并流式执行（stream_run 为同步生成器，桥接为异步流）----
        # build() 内部会构造 MCPTool，而 MCPTool.__init__ 的 _discover_tools() 是
        # 无超时的阻塞网络调用；必须放到工作线程，否则会冻住事件循环导致全部接口挂起。
        # （MCP 配置已在上面于主循环预取，build 不再触碰 DB，可安全跨线程）
        # 每个意图新建 SimpleAgent 实例，避免多意图共享 _history 相互污染
        build_started_ms = collector.now_ms() if collector else 0
        simple_agent = await asyncio.to_thread(business_agent.build, mcp_configs)
        if collector:
            registry = getattr(simple_agent, "tool_registry", None)
            # available_tools = 模型本轮真正能调的工具（含 MCP 展开后的独立工具）；
            # 与 declared_mcp 对比即可看出“MCP 连上了但没展开出工具”这类静默失败
            available = sorted(registry.list_tools()) if registry is not None else []
            model = getattr(business_agent, "llm_model", "") or settings.llm_model_id
            collector.add(
                SPAN_BUILD,
                agent_name,
                started_ms=build_started_ms,
                status="ok" if available or not business_agent.mcp_tools else "empty",
                intent_index=intent_index,
                llm_model=model,
                mcp_connected=sorted(
                    n for n, cfg in (mcp_configs or {}).items() if cfg is not None
                ),
                available_tools=available,
            )
            collector.add_unique("llm_models", model)
        async for event in self._astream(simple_agent, enriched_query):
            yield event

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
