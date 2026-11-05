"""
Orchestrator：调度器
  0. 高频固定话术（身份/能力/探听配置）命中时直接下发硬模板，后续步骤全部跳过
  1. 调用 RouterAgent 识别意图（可多意图）
  2. 从注册表取出业务 Agent 类
  3. 实例化业务 Agent
  4. 构建 SimpleAgent 并流式执行（含工具调用循环）；多意图时按主次串行执行并用
     agent_start / agent_end 事件分块下发
"""
import asyncio
import inspect
import logging
import threading
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from app.agents.canned_scripts import (
    ANSWER_SOURCE_KB,
    match_canned_script,
    render_script_text,
)
from app.agents.registry import AGENT_REGISTRY, resolve_business_agent
from app.agents.router_agent import RouterAgent
from app.agents.scene_router import build_scene_router_profile
from app.core.config import settings
from app.core.context_engineer import ContextEngineer
from app.core.ports.adapters.hello_agents import Message, SimpleAgent
from app.core.tracing import (
    OUTCOME_ANSWERED,
    OUTCOME_ERROR,
    OUTCOME_HANDOVER,
    SPAN_BUILD,
    SPAN_CANNED,
    SPAN_RESOLVE,
    SPAN_ROUTE,
    SPAN_RUN,
)
from app.schemas.scene import SceneContext, SceneRouterProfile
from app.services.canned_answer import answer_from_kb
from app.services.runtime_config import effective_runtime

logger = logging.getLogger(__name__)

# 全局默认阈值（无场景档案时的回退值）；场景档案非空时以档案为准。
# 保留模块级常量：既有调用方/测试通过 monkeypatch 覆盖它来固定行为。
CONFIDENCE_THRESHOLD = settings.router_confidence_threshold

_SENTINEL = object()

# SimpleAgent 的文本 ReAct 标记前缀
_TOOL_MARKER = "[TOOL_CALL:"

# 多意图分块之间插入的分隔（markdown 水平线，前端逐字渲染即可分段）
_INTENT_SEPARATOR = "\n\n---\n\n"

# 升级人工类工具：本轮只要成功执行过它们，就在文本之外补发 handover 事件，
# 由 chat_service 把会话置为排队待接入（与低置信整轮转人工同一契约）。
# 判定不解析工具返回体（_execute_tool 已把它包装成带前缀的文本），只看工具名 + 无错误。
_ESCALATION_TOOLS = frozenset({"escalate", "escalate_it"})

# 注入业务 Agent 的最近对话轮数上限（业务 Agent 每轮新建，靠此获得跨轮记忆）
_HISTORY_WINDOW = 12


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


# 自定义工具默认执行超时（秒）；BaseTool 子类可用 timeout 类属性覆盖
_DEFAULT_TOOL_TIMEOUT = 30

# 同步工具（MCP 包装工具）执行线程池。其调用链（MCPWrappedTool.run → MCPTool.run 的
# future.result() → MCPClient.call_tool）全程没有超时，服务端不响应时会永久阻塞；
# 工作线程无法被安全取消，只能限制并发并在超时后丢弃该线程，避免线程/子进程随请求累积。
_SYNC_TOOL_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool-sync")

# 流式桥接空闲超时（秒）：队列超过该时长没有任何事件即判定生产侧卡死。
# 正常最坏静默为「工具硬超时 30s + 单次 LLM 调用库默认超时 60s」，120s 留有余量。
_STREAM_IDLE_TIMEOUT = 120

# 流式桥接队列上限：消费端（客户端连接）已断开但生产侧未察觉时，最多积压这么多事件，
# 超过即丢弃积压并立即下发终止哨兵，避免无界队列持续吃内存
_STREAM_QUEUE_MAXSIZE = 256


def _execute_tool(
    agent: SimpleAgent,
    tool_name: str,
    args: dict[str, Any],
    context: dict[str, Any] | None,
    loop: asyncio.AbstractEventLoop | None,
) -> str:
    """项目级工具执行器，替代 hello_agents 的同步 `_execute_tool_call`。

    hello_agents 用同步 `tool.run(参数字典)` 调用工具，只适配 MCP 包装工具；
    本项目的自定义工具是 `async def run(**kwargs)`（见 custom/base.py），若沿用
    同步调用会返回未 await 的协程（或直接 TypeError），工具根本不执行、不落库。
    这里统一两种形态：
    - 协程函数：绑定 kwargs，并回投到主事件循环执行（DB 连接池绑定主循环，
      不能在工作线程里另起 loop），用 run_coroutine_threadsafe + 超时等待结果；
    - 普通函数（MCP 包装工具）：按 hello_agents 约定传参数字典。
    对模型不会提供的内部参数（user_id/session_id），若工具声明了则从会话上下文注入。
    """
    registry = agent.tool_registry
    tool = registry.get_tool(tool_name) if registry is not None else None
    if tool is None:
        return f"❌ 错误：未找到工具 '{tool_name}'"

    args = dict(args or {})
    context = context or {}
    # 注入会话上下文：仅补齐工具显式声明、且模型未给出的 user_id/session_id
    try:
        declared = {p.name for p in tool.get_parameters()}
    except Exception:  # noqa: BLE001
        declared = set()
    for key in ("user_id", "session_id"):
        if key in declared and not args.get(key) and context.get(key):
            args[key] = context[key]

    try:
        if inspect.iscoroutinefunction(tool.run):
            timeout = getattr(tool, "timeout", None) or _DEFAULT_TOOL_TIMEOUT
            if loop is None:
                # 无主循环可回投（单测/脚本直调）：同样要上硬超时，否则工具声明的
                # timeout 在这条路径上会被静默忽略，一直等到自己跑完。
                try:
                    result = asyncio.run(asyncio.wait_for(tool.run(**args), timeout))
                except TimeoutError:
                    raise TimeoutError(f"工具 {tool_name} 执行超时（{timeout}s），已取消") from None
            else:
                future = asyncio.run_coroutine_threadsafe(tool.run(**args), loop)
                try:
                    result = future.result(timeout)
                except FutureTimeout:
                    # cancel() 线程安全：取消会调度回主循环，阻止工具在超时后继续
                    # 执行/写库（否则模型拿到“失败”后重试可能重复建单）
                    future.cancel()
                    raise TimeoutError(f"工具 {tool_name} 执行超时（{timeout}s），已取消") from None
        else:
            # 同步工具（如 MCPWrappedTool）：按 hello_agents 约定接收参数字典。
            # MCP 调用链内部没有任何超时，这里用有界线程池做硬超时兜底：超时即放弃
            # 等待，返回失败结果让模型继续本轮的后续生成，保证 SSE 一定能走到 done。
            timeout = getattr(tool, "timeout", None) or _DEFAULT_TOOL_TIMEOUT
            future = _SYNC_TOOL_POOL.submit(tool.run, args)
            try:
                result = future.result(timeout)
            except FutureTimeout:
                future.cancel()  # 运行中的线程实际无法取消，仅取消排队任务并放弃等待
                raise TimeoutError(f"工具 {tool_name} 执行超时（{timeout}s），已放弃等待") from None
        return f"🔧 工具 {tool_name} 执行结果：\n{result}"
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"工具执行失败: {tool_name}")
        return f"❌ 工具调用失败：{str(exc)}"


def _stream_with_tools(
    agent: SimpleAgent,
    input_text: str,
    context: dict[str, Any] | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
    max_tool_iterations: int = 3,
    stop: threading.Event | None = None,
) -> Iterator[dict]:
    """流式执行 + 真正的工具调用循环（同步生成器，产出 dict 事件）。

    hello_agents 的 stream_run 只做一次 LLM 流式调用、完全没有工具循环，
    模型会把 [TOOL_CALL:...] 和 <result>{...}</result> 一起“演”出来（假数据）。
    这里对齐 SimpleAgent.run 的语义自行实现循环，并区分下发内容：
    - 中间轮（含工具调用）的原始文本不下发，只发 tool_call / tool_result 事件；
    - 最终轮文本仍逐块流式下发（无工具调用时 UX 不变）。

    stop：客户端断开 / 流已判超时后置位，各循环在每个 chunk / 工具调用前检查并
    立即停止——否则连接断开后仍会继续消耗 LLM token 直到生成自然结束。
    """
    def _aborted() -> bool:
        return stop is not None and stop.is_set()

    messages: list[dict] = [
        {"role": "system", "content": agent._get_enhanced_system_prompt()}
    ]
    messages.extend({"role": m.role, "content": m.content} for m in agent._history)
    messages.append({"role": "user", "content": input_text})

    tool_mode = bool(getattr(agent, "enable_tool_calling", False)) and agent.tool_registry is not None

    if not tool_mode:
        full = ""
        for chunk in agent.llm.stream_invoke(messages):
            if _aborted():
                return
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
            if _aborted():
                return
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
            if _aborted():
                return
            call_id = f"call-{iteration}-{idx}"
            tool_name = call["tool_name"]
            raw_args = call["parameters"]
            try:
                args = agent._parse_tool_parameters(tool_name, raw_args)
            except Exception:  # noqa: BLE001
                args = {"raw": raw_args}
            yield {"type": "tool_call", "data": {"id": call_id, "name": tool_name, "args": args}}

            result = _execute_tool(agent, tool_name, args, context, loop)
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
        # 单轮内的上下文缓存：多意图共享同一 query，避免重复做记忆/知识库检索
        self._ctx_cache: dict[tuple[str, str, tuple[str, ...]], str] = {}

    @staticmethod
    async def _astream(
        simple_agent: SimpleAgent, query: str, context: dict[str, Any] | None = None
    ) -> AsyncIterator[dict]:
        """将 SimpleAgent.stream_run（同步生成器）桥接为异步流。

        后台线程逐条产出事件，通过线程安全的方式回投到事件循环的队列，
        避免阻塞事件循环同时保留逐个流式送达的语义。
        工具执行（协程型自定义工具）回投到本 loop 执行，保证 DB 连接池可用。
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=_STREAM_QUEUE_MAXSIZE)
        # 消费端退出信号：客户端断开 / 空闲超时 / 正常结束都会置位，通知生产线程停止
        stop = threading.Event()

        def _offer(event: Any) -> None:
            """事件循环线程内入队；stop 后丢弃非终止事件，队列满则直接收尾。"""
            if event is not _SENTINEL and stop.is_set():
                return
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 消费端严重滞后（多半已断开）：丢弃积压并立即下发终止哨兵
                while not queue.empty():
                    queue.get_nowait()
                stop.set()
                queue.put_nowait(_SENTINEL)

        def _put(event: Any) -> None:
            """把事件投回事件循环；循环已关闭（请求已结束后台线程收尾）时静默丢弃。"""
            try:
                loop.call_soon_threadsafe(_offer, event)
            except RuntimeError:
                pass

        def _pump() -> None:
            try:
                for event in _stream_with_tools(
                    simple_agent, query, context=context, loop=loop, stop=stop
                ):
                    _put(event)
            except Exception as exc:  # noqa: BLE001
                logger.exception("stream_run 执行异常")
                _put({"type": "error", "data": {"code": 5001, "message": str(exc)}})
            finally:
                _put(_SENTINEL)

        threading.Thread(target=_pump, daemon=True).start()

        try:
            while True:
                try:
                    # 空闲看门狗：生产侧（LLM 流 / MCP 工具）卡死时队列永远不会再有事件，
                    # 无超时的 get() 会让整个 SSE 请求永久挂起；超时即发 error 收尾本轮。
                    event = await asyncio.wait_for(queue.get(), timeout=_STREAM_IDLE_TIMEOUT)
                except TimeoutError:
                    logger.error(f"流式生成空闲超时({_STREAM_IDLE_TIMEOUT}s)，已中止本轮")
                    yield {"type": "error", "data": {"code": 5004, "message": "生成超时，请稍后重试"}}
                    return
                if event is _SENTINEL:
                    break
                # hello_agents SimpleAgent.stream_run 产出的是纯文本块，
                # 统一归一化为 dict 事件，保证下游 dispatch()/SSE 处理契约一致。
                if isinstance(event, str):
                    yield {"type": "text", "data": {"content": event}}
                else:
                    yield event
        finally:
            # 正常结束 / 空闲超时 / 客户端断开取消都会到这里：通知生产线程停止，
            # 避免断开后继续消耗 LLM token，并避免事件堆入无人消费的队列
            stop.set()

    @staticmethod
    async def _resolve_canned_answer(
        scene: SceneContext | None,
        canned,
        query: str,
        collector=None,
    ) -> str:
        """取一条命中话术本轮要下发的正文；空串表示本轮继续走路由。

        - text 模式：渲染占位符后直接下发（毫秒级，不查库）；
        - kb 模式：检索绑定的知识库并按固定框架拼接，零命中/超时/异常都回退到正文。
        """
        rendered = render_script_text(canned.text, scene)
        if canned.answer_source != ANSWER_SOURCE_KB:
            if collector:
                collector.add(
                    SPAN_CANNED,
                    canned.key,
                    category=canned.category,
                    answer_source=canned.answer_source,
                    text_chars=len(rendered),
                )
            return rendered

        kb = await answer_from_kb(scene, canned, effective_runtime(scene), query)
        answer = kb.text or rendered
        if collector:
            collector.add(
                SPAN_CANNED,
                canned.key,
                category=canned.category,
                answer_source="kb",
                kb_namespaces=list(kb.namespaces),
                kb_chunks=kb.chunks,
                kb_status=kb.status,
                text_chars=len(answer),
                status="error" if kb.status == "error" else "ok",
            )
            # 知识库命中的分块计入本轮 RAG 统计（与 ContextEngineer 自动注入同一口径）
            collector.rag_chunk_count += kb.chunks
            for ns in kb.namespaces:
                collector.add_unique("rag_namespaces", ns)
        return answer

    async def dispatch(
        self,
        user_id: str,
        session_id: str,
        query: str,
        history: list[dict],
        user_message_id: str | None = None,
        last_route_to: str | None = None,
        max_intents: int | None = None,
        collector=None,
        scene: SceneContext | None = None,
        profile: SceneRouterProfile | None = None,
    ) -> AsyncIterator[dict]:
        """一轮对话的完整调度。

        - history / last_route_to 一并交给路由，使追问能续接上一轮的 Agent
        - max_intents：本轮最多执行的意图数；优先级：显式参数 > 场景档案 > 全局默认
        - user_message_id：本轮用户消息 id，归档记忆时写入溯源列
        - collector：链路采集器（app.core.tracing.TraceCollector），为空则不采集
        - scene：场景快照；M3 起由它派生路由档案（mode=direct 时跳过路由 LLM）
        - profile：已构建好的场景路由档案；不传则按 scene 现场派生，保证两者同源
        """
        if scene is not None and profile is None and scene.candidates:
            # 兼容旧调用方只传 scene：候选非空时按同一逻辑派生档案；
            # 候选为空（手工构造的旧测试快照）则保持旧的 direct_agent 语义。
            profile = build_scene_router_profile(scene)
        max_intents = max(
            1,
            int(
                max_intents
                if max_intents is not None
                else (profile.max_intents if profile is not None else settings.router_max_intents)
            ),
        )
        # 阈值随场景覆盖：主意图不达标本轮转人工
        threshold = (
            profile.confidence_threshold if profile is not None else CONFIDENCE_THRESHOLD
        )
        # 运行参数只解析一次：同一轮所有意图/环节共享，避免热路径重复合并全局默认
        runtime = effective_runtime(scene)
        self._ctx_cache.clear()

        # ---- 0. 高频固定话术（身份 / 能力 / 探听配置）：命中则逐字下发硬模板 ----
        # 不进路由、不建 Agent：措辞完全可控，同时省掉路由 LLM + MCP 建连 + RAG/记忆检索的数秒等待。
        # 不发 route 事件——本轮确实没有 Agent 参与，last_route_to 保留上一轮的真实承接方，
        # 后续追问续接才不会被一个并不存在的 Agent 名干扰；记忆归档也一并跳过（无业务信息量）。
        # 话术按场景合并：全局条（如探听拒答）+ 本场景覆盖条。
        canned = await match_canned_script(
            query, scene.scene_id if scene is not None else None
        )
        if canned is not None:
            # 知识库模式需要一次异步检索：先发状态事件，避免 1–3 秒静默
            if canned.answer_source == ANSWER_SOURCE_KB:
                yield {
                    "type": "status",
                    "data": {"stage": "preparing", "text": "正在查询知识库…"},
                }
            answer = await self._resolve_canned_answer(scene, canned, query, collector)
            if answer:
                logger.info(f"[canned] 命中固定话术 {canned.key}，跳过路由与 Agent 执行")
                if collector:
                    collector.mark_first_token()
                    collector.record_outcome(OUTCOME_ANSWERED)
                yield {"type": "text", "data": {"content": answer}}
                return
            # 知识库零命中且没有兜底文案：不拦截本轮，继续走路由
            logger.info(f"[canned] 命中 {canned.key} 但无可用答案，本轮继续正常路由")

        # ---- 1. 路由（场景直连则跳过；否则可多意图） ----
        direct_agent = (
            profile.direct_agent
            if profile is not None
            else (scene.direct_agent if scene is not None else None)
        )
        scene_code = scene.code if scene is not None else None
        if direct_agent:
            # 直连不做意图识别：0 次路由 LLM，首字延迟只受业务 Agent 自身影响
            intents = [
                {
                    "agent_name": direct_agent,
                    "confidence": 1.0,
                    "reason": "scene_direct",
                    "is_fallback": False,
                }
            ]
            fallback_agent = direct_agent
            if collector:
                collector.router_skipped = True
                # 直连没有路由 LLM，但同样记录配置指纹：链路能解释“这轮用的哪份路由配置”
                collector.prompt_hash = profile.prompt_hash if profile is not None else None
                collector.record_intents(intents, max_intents)
                collector.add(
                    SPAN_ROUTE,
                    direct_agent,
                    status="ok",
                    scene_code=scene_code,
                    router_skipped=True,
                    router_mode=profile.mode if profile is not None else "direct",
                    candidate_count=len(profile.candidates) if profile is not None else None,
                    prompt_hash=profile.prompt_hash if profile is not None else None,
                )
            logger.info(f"[route] 场景直连 scene={scene_code} agent={direct_agent}")
        else:
            # status 事件：前端"思考中"占位气泡的真实阶段进度（不计入 produced 内容）
            yield {"type": "status", "data": {"stage": "routing", "text": "正在分析问题意图…"}}
            route_kwargs = {
                "history": history,
                "last_route_to": last_route_to,
                "collector": collector,
            }
            if profile is not None:
                route_kwargs["profile"] = profile
            intent_result = await self.router.route(query, **route_kwargs)
            intents: list[dict] = intent_result["intents"]
            # 本轮生效的兜底 Agent（由路由层从生效清单派生，避免指向已禁用的 Agent）
            fallback_agent = intent_result.get("fallback_agent")
            # 候选边界：档案已限定提示词范围，这里做最后一道闸（防模型臆造名称）
            allowed = (
                {c["name"] for c in profile.candidates if c.get("name")}
                if profile is not None
                else (set(scene.agent_names) if scene is not None and scene.agent_names else set())
            )
            if allowed:
                intents = [i for i in intents if i["agent_name"] in allowed]
                if not intents:
                    intents = [
                        {
                            "agent_name": fallback_agent or next(iter(allowed)),
                            "confidence": 0.0,
                            "reason": "scene_fallback",
                            "is_fallback": True,
                        }
                    ]
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
                "scene_code": scene_code,
                "router_skipped": bool(direct_agent),
                # M3：场景路由档案的可观测字段（同一 prompt_hash 即同一路由配置）
                "mode": profile.mode if profile is not None else None,
                "candidate_count": len(profile.candidates) if profile is not None else None,
                "prompt_hash": profile.prompt_hash if profile is not None else None,
            },
        }

        # ---- 2. 低置信度 → 转人工（主意图不达标则整轮转人工；
        #          仅次要意图不达标则丢弃该分支、主意图照常应答）----
        qualified = [i for i in intents if i["confidence"] >= threshold]
        if not qualified:
            # 例外：兜底意图的 confidence 是提示词约定的固定低值（见 router_agent.FALLBACK_CONFIDENCE），
            # 不属于“模型拿不准”，默认交给兜底 Agent 应答而不是整轮转人工（可用 ROUTER_FALLBACK_ANSWER 关闭）
            fallback_intents = [i for i in intents if i.get("is_fallback")]
            if settings.router_fallback_answer and fallback_intents:
                qualified = fallback_intents
                logger.info(f"路由未命中具体业务，由兜底 Agent 应答: {fallback_intents[0]['agent_name']}")
            else:
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
        fallback_attempted = False
        escalated = False
        for intent in targets:
            intent_name = intent["agent_name"]
            # 代码注册表优先，其次 DB 动态 Agent
            business_agent = await resolve_business_agent(
                intent_name, user_id=user_id, session_id=session_id
            )
            if business_agent is None and not fallback_attempted:
                # 意图 Agent 不可用（后台禁用/已删除/模型臆造名称）：回落一次兜底 Agent，
                # 而非直接给客户的整轮报 5006
                if (
                    fallback_agent
                    and fallback_agent != intent_name
                    and fallback_agent not in handled
                ):
                    fallback_attempted = True
                    logger.warning(f"意图 {intent_name} 的 Agent 不可用，回落兜底 Agent: {fallback_agent}")
                    business_agent = await resolve_business_agent(
                        fallback_agent, user_id=user_id, session_id=session_id
                    )
                    if business_agent is not None:
                        intent_name = fallback_agent
                        intent = {**intent, "agent_name": fallback_agent}
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
                business_agent,
                user_id,
                query,
                session_id=session_id,
                history=history,
                collector=collector,
                intent_index=index,
                runtime=runtime,
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
                elif etype == "tool_result":
                    if collector:
                        collector.finish_tool_call(
                            open_tools.pop(data.get("id"), ""),
                            result=data.get("result"),
                            error=data.get("error"),
                        )
                    # 升级工具成功执行：记下来，本轮收尾补发 handover
                    # （工具失败/异常时 _execute_tool 返回错误文本但 error 为 None，
                    #  靠“结果文本含 handover 标记”过滤：成功结果里才有这个键）
                    if (
                        data.get("name") in _ESCALATION_TOOLS
                        and data.get("error") is None
                        and "handover" in str(data.get("result") or "")
                    ):
                        escalated = True
                elif etype == "text":
                    text_chars += len(data.get("content") or "")
                    if collector:
                        collector.mark_first_token()
                # 进度提示不算产出：只有真实内容/工具事件才视为该分支应答成功
                if etype != "status":
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

        # 升级工具已执行：补发 handover 事件（Agent 终轮文本照常保留），
        # chat_service 据此把会话置 handover 进待接入队列
        if escalated:
            if collector:
                collector.record_outcome(OUTCOME_HANDOVER, reason="escalate")
            yield {"type": "handover", "data": {"reason": "escalated_by_tool"}}
            return

        if not streamed:
            if collector:
                collector.record_outcome(OUTCOME_ERROR, code=5006, message="Agent 未注册")
                collector.mark_finished()
            yield {"type": "error", "data": {"code": 5006, "message": "Agent 未注册"}}
            return

        if collector:
            collector.record_outcome(OUTCOME_ANSWERED)

        # ---- 7. 归档记忆（一轮只归档一次，记在主意图名下；主意图回落时记实际承接的 Agent） ----
        # 放到后台执行：两次 embedding + 镜像 upsert 不应拦住 done 事件与连接释放。
        # FakeCtx（测试）只实现 persist_after_run 时保留 await 回退，保证接口兼容。
        archive_kwargs = dict(
            user_id=user_id,
            session_id=session_id,
            query=query,
            agent_name=handled[0] if handled else agent_name,
            history=history,
            message_id=user_message_id,
            collector=collector,
        )
        schedule = getattr(self.ctx_engineer, "schedule_after_run", None)
        if schedule is not None:
            # 后台归档不再往共享 collector 写 span：与 trace 落库并发会造成 span 时有时无
            schedule(**{**archive_kwargs, "collector": None})
        else:
            await self.ctx_engineer.persist_after_run(**archive_kwargs)

    async def _run_business_agent(
        self,
        business_agent,
        user_id: str,
        query: str,
        session_id: str = "",
        history: list[dict] | None = None,
        collector=None,
        intent_index: int = 0,
        runtime=None,
    ) -> AsyncIterator[dict]:
        """构建并流式执行单个业务 Agent（含 MCP / 动态工具预取与上下文工程）。"""
        agent_name = getattr(business_agent, "name", "?")
        yield {"type": "status", "data": {"stage": "preparing", "text": "正在连接工具与检索知识库…"}}
        # ---- 在主 event loop 上预取 MCP 配置（避免 build() 跨循环访问 DB）----
        mcp_configs: dict[str, dict | None] = {}
        if business_agent.mcp_tools:
            from app.tools.mcp_loader import fetch_mcp_configs

            mcp_configs = await fetch_mcp_configs(business_agent.mcp_tools)
            if collector:
                for name in business_agent.mcp_tools:
                    collector.add_unique("mcp_server_names", name)

        # ---- 同样在主循环预取「动态自定义工具」源码（后台写的 config.code）----
        # 代码目录工具不产生 DB 访问；预取失败也不能影响对话，异常在此兜住降级为空配置。
        custom_configs: dict[str, dict] = {}
        if getattr(business_agent, "custom_tool_names", None):
            from app.tools.custom_loader import fetch_dynamic_configs

            try:
                custom_configs = await fetch_dynamic_configs(business_agent.custom_tool_names)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"⚠️ 动态自定义工具配置预取失败，本轮仅装配代码目录工具: {exc}")

        # ---- 上下文工程 ----
        # 多意图共享同一 query：按 (用户, query, 命名空间) 缓存，第二个意图不再重复检索
        rag_namespaces = await self._resolve_rag_namespaces(business_agent, runtime)
        cache_key = (user_id, query, tuple(rag_namespaces))
        enriched_query = self._ctx_cache.get(cache_key)
        if enriched_query is None:
            enriched_query = await self.ctx_engineer.build(
                user_id=user_id,
                query=query,
                rag_namespaces=rag_namespaces,
                collector=collector,
                runtime=runtime,
            )
            self._ctx_cache[cache_key] = enriched_query

        # ---- 构建 SimpleAgent 并流式执行（stream_run 为同步生成器，桥接为异步流）----
        # build() 内部会构造 MCPTool，而 MCPTool.__init__ 的 _discover_tools() 是
        # 无超时的阻塞网络调用；必须放到工作线程，否则会冻住事件循环导致全部接口挂起。
        # （MCP 配置已在上面于主循环预取，build 不再触碰 DB，可安全跨线程）
        # 每个意图新建 SimpleAgent 实例，避免多意图共享 _history 相互污染
        build_started_ms = collector.now_ms() if collector else 0
        simple_agent = await asyncio.to_thread(business_agent.build, mcp_configs, custom_configs)
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
        # 把最近若干轮对话注入 Agent._history，赋予跨轮记忆：业务 Agent 每轮新建，
        # 不注入则模型看不到上一轮，既接不住追问也不知道刚创建的工单号（无法 update_ticket）
        self._seed_history(simple_agent, history, collector)
        if collector:
            # 用量口径：上下文工程拼装后的提示词（多意图各计一次）；
            # 历史由 _seed_history 按实际注入内容逐条计入
            collector.record_prompt(enriched_query)
        yield {"type": "status", "data": {"stage": "generating", "text": "正在生成回答…"}}
        async for event in self._astream(
            simple_agent,
            enriched_query,
            context={"user_id": user_id, "session_id": session_id},
        ):
            yield event

    @staticmethod
    def _seed_history(
        simple_agent: SimpleAgent, history: list[dict] | None, collector=None
    ) -> None:
        """用最近几轮历史填充 SimpleAgent._history（_stream_with_tools 会读取它）。

        传 collector 时顺带把这些内容计入用量：历史确实被送进了模型，
        口径放在这里可以保证与实际注入内容一致，不会与过滤/窗口逻辑跑偏。
        """
        if not history:
            return
        prior = list(history)
        # 当前这轮的用户消息已在生成前入库，会出现在 history 末尾，需剔除以免与本轮输入重复
        if prior and prior[-1].get("role") == "user":
            prior = prior[:-1]
        for h in prior[-_HISTORY_WINDOW:]:
            role = h.get("role")
            content = (h.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                simple_agent._history.append(Message(content, role))
                if collector is not None:
                    collector.record_prompt(content)

    @staticmethod
    async def _resolve_rag_namespaces(business_agent, runtime=None) -> list[str]:
        """解析业务 Agent 实际检索的知识库命名空间列表。

        知识库不记场景归属：检索范围默认由 Agent 自己声明，同库可被多个场景的 Agent 复用。

        优先级：
        - 场景运行参数显式配置了 rag.namespaces：以场景为准（后台已校验库存在且启用，
          否则“来源=本场景覆盖”的展示与实际行为不一致）；
        - search_all_knowledge_bases=True：覆盖全部启用知识库（后台默认不开启该开关，
          跨业务检索靠 namespace 命名与配置纪律控制）；
        - 否则：rag_namespace（单库）与 rag_namespaces（显式多库）合并去重，按声明顺序检索。
        """
        if runtime is not None and runtime.rag_namespaces:
            return list(runtime.rag_namespaces)

        own = business_agent.rag_namespace
        extra = list(getattr(business_agent, "rag_namespaces", []) or [])

        def _dedupe(seq: list[str | None]) -> list[str]:
            seen: set[str] = set()
            ordered: list[str] = []
            for ns in seq:
                if ns and ns not in seen:
                    seen.add(ns)
                    ordered.append(ns)
            return ordered

        if getattr(business_agent, "search_all_knowledge_bases", False):
            from app.models.repositories import KnowledgeBaseRepo

            bases = await KnowledgeBaseRepo.list_all()
            namespaces = [b.namespace for b in bases if b.is_active]
            # 自身默认空间与显式多库前置（优先级更高），再补齐其余启用库
            return _dedupe(([own] if own else []) + extra + namespaces)
        resolved = _dedupe(([own] if own else []) + extra)
        return resolved
