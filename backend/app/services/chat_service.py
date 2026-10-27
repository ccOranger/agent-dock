"""ChatService：编排对话流程并输出 SSE 事件流"""
import logging
from collections.abc import AsyncIterator

from app.agents.orchestrator import Orchestrator
from app.core.constants import SessionStatus
from app.core.database import SessionLocal
from app.core.exceptions import UnsafeInput
from app.core.security import detect_injection, sanitize_output
from app.core.tracing import (
    OUTCOME_ERROR,
    OUTCOME_HANDOVER,
    TraceCollector,
    new_request_trace_id,
)
from app.models.repositories import (
    ChatEventRepo,
    MessageRepo,
    SessionRepo,
    TraceRepo,
    VisitorRepo,
)
from app.schemas.scene import Actor, ActorType, SceneContext
from app.services.runtime_config import effective_runtime
from app.services.scene_service import resolve_scene
from app.utils.sse import sse_event

logger = logging.getLogger(__name__)

# 每轮对话仅取最近 N 条历史作为路由/跨轮记忆上下文，避免长会话全量加载；
# 默认值与上限统一由场景运行参数管理（schemas/scene.py: DEFAULT_HISTORY_LIMIT）

# 与 SessionRepo/entities 中 Session.title 的默认值保持一致：
# 只要标题仍是这个占位值，就认为会话还没被命名过，可以用首条消息自动补全
DEFAULT_SESSION_TITLE = "新对话"


def derive_session_title(message: str, max_len: int = 24) -> str:
    """用用户首条消息生成会话标题：折叠换行/连续空白，超长截断。"""
    cleaned = " ".join(message.split())
    if not cleaned:
        return ""
    return cleaned[:max_len] + ("…" if len(cleaned) > max_len else "")


# 坐席接管期间的提示事件文案：不跑 Agent，客户消息只落库进坐席队列
TAKEOVER_WAITING_TEXT = "人工客服正在为您服务，您的消息已转达。"

# 客户明确要求转人工的触发词：整句包含即命中，走确定性转人工（不走大模型）。
# default_assistant 等未挂 escalate 工具的 Agent 只会用文本“扮演”转人工、从不产生
# handover 事件，这里补上真正的触发口，保证任何场景说“转人工”都能进后台待接入队列。
# 词表刻意收紧为“人工/真人 + 转/接/客服”这类强指向组合，避免“人工智能”等误命中。
HANDOVER_KEYWORDS = (
    "转人工",
    "转接人工",
    "接人工",
    "人工客服",
    "真人客服",
    "找人工",
    "人工服务",
)

# 命中触发词后回给客户的首句确认（坐席后续回复经轮询下发）
HANDOVER_CONFIRM_TEXT = "好的，正在为您转接人工客服，请稍候。"


def _is_explicit_handover(text: str) -> bool:
    return any(kw in text for kw in HANDOVER_KEYWORDS)


class ChatService:
    def __init__(self):
        self.orchestrator = Orchestrator()

    @staticmethod
    async def _persist_trace(collector: TraceCollector) -> None:
        """链路写入是旁路：失败只记日志，绝不影响对话（与记忆归档同一策略）。"""
        if not collector.should_persist():
            return
        try:
            await TraceRepo.insert(collector.to_row())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"链路写入失败（不阻塞主流程）: {e}")

    @staticmethod
    async def _persist_usage(
        collector: TraceCollector, integration_id: str | None, *, error: bool = False
    ) -> None:
        """用量记账：明细回填 + 日聚合，失败只告警。

        不经过 should_persist()：链路可以按采样丢弃，账不能。
        """
        tokens = collector.total_tokens
        try:
            await ChatEventRepo.record_usage(
                trace_id=collector.trace_id,
                scene_code=collector.scene_code or "default",
                integration_id=integration_id,
                tokens=tokens,
                # hello_agents 不回传 usage，当前口径全是估算；接入真实 usage 后改为按来源判断
                estimated=tokens > 0,
                handover=collector.outcome == OUTCOME_HANDOVER,
                error=error,
            )
        except Exception as exc:  # noqa: BLE001 - 记账旁路不得阻塞对话
            logger.warning(f"用量记账失败（不阻塞对话）: {exc}")

    @staticmethod
    async def _attach_chat_event_identity(trace_id: str | None, session) -> None:
        """把真实 session/visitor 回填到请求级埋点，失败只告警。

        埋点在进入 service 前写入，首轮 session_id 与匿名访客的 visitor_id 还是空；
        这里按 trace_id 补一次，chat_events 才能 join 会话与访客做统计。
        """
        if not trace_id:
            return
        try:
            await ChatEventRepo.attach_identity(
                trace_id=trace_id,
                session_id=str(session.id),
                visitor_id=str(session.visitor_id) if session.visitor_id else None,
                user_id=str(session.user_id) if session.user_id else None,
            )
        except Exception as exc:  # noqa: BLE001 - 埋点旁路不得阻塞对话
            logger.warning(f"对话埋点身份回填失败（不阻塞对话）: {exc}")

    async def stream(
        self,
        user_id: str | None,
        session_id: str | None,
        message: str,
        trace_id: str | None = None,
        scene_code: str | None = None,
        visitor_id: str | None = None,
        integration_id: str | None = None,
        scene: SceneContext | None = None,
    ) -> AsyncIterator[str]:
        # 场景在会话之前解析：错码/停用/未配置直接失败，不产生空会话；
        # 路由层已预校验时直接复用快照，避免一轮请求重复查两次场景。
        scene = scene or await resolve_scene(scene_code)
        actor = await self._resolve_actor(scene, user_id, visitor_id, integration_id)
        async with SessionLocal() as db:
            session = await SessionRepo.get_or_create_for_actor(
                actor, scene.scene_id, session_id, db=db
            )
            # 新建会话默认标题是占位值"新对话"，历史列表里区分度差；
            # 用户手动重命名过（不再是占位值）的会话不覆盖。
            if session.title == DEFAULT_SESSION_TITLE:
                derived = derive_session_title(message)
                if derived:
                    session.title = derived
            user_msg = await MessageRepo.add(session.id, role="user", content=message, db=db)
            # 用户消息先落库：后续异常 / 客户端断连也不丢用户输入
            await db.commit()
            await self._attach_chat_event_identity(trace_id, session)

            # 采集本轮经过的 Agent / MCP / 工具 / RAG / 记忆，流结束后一次写入 request_traces
            collector = TraceCollector(
                trace_id=trace_id or new_request_trace_id(),
                user_id=str(session.user_id) if session.user_id else None,
                session_id=str(session.id),
                scene_code=scene.code,
                visitor_id=str(session.visitor_id) if session.visitor_id else None,
            )
            collector.user_message_id = str(user_msg.id)

            yield sse_event(
                "session", {"session_id": str(session.id), "scene_code": scene.code}
            )

            # 坐席接管中：客户消息只落库不走 Agent 编排，机器人让位避免人机双应答
            if session.status in (SessionStatus.HUMAN_HANDLING, SessionStatus.HANDOVER):
                # touch 会 commit 并过期对象，状态先取到局部变量再访问
                takeover_status = session.status
                # 接管期间不产生助手消息，需显式刷新 updated_at：
                # 坐席队列按「最近动态」排序，新追问的会话应排在前部
                await SessionRepo.touch(session.id, db=db)
                collector.record_outcome(OUTCOME_HANDOVER, reason=takeover_status)
                collector.mark_finished()
                await self._persist_usage(collector, integration_id)
                await self._persist_trace(collector)
                yield sse_event(
                    "status",
                    {
                        "stage": "takeover",
                        "text": TAKEOVER_WAITING_TEXT,
                        "session_status": takeover_status,
                    },
                )
                yield sse_event(
                    "done",
                    {
                        "message_id": str(user_msg.id),
                        "trace_id": collector.trace_id,
                        "takeover": True,
                        "session_status": takeover_status,
                    },
                )
                return

            # 客户明确要求转人工：确定性置 handover 进后台待接入队列，不走大模型
            # （低置信/escalate 两条既有触发口对无路由器的默认场景、对未挂
            #  升级工具的 Agent 都覆盖不到，这里补上确定性的第三条）
            if session.status == SessionStatus.ACTIVE and _is_explicit_handover(message):
                session.status = SessionStatus.HANDOVER
                await db.commit()
                collector.record_outcome(OUTCOME_HANDOVER, reason="keyword")
                collector.mark_finished()
                await self._persist_usage(collector, integration_id)
                await self._persist_trace(collector)
                # 用 text 事件承载确认：ChatView 的占位气泡只在收到正文时才创建，
                # 单发 status 会被 setStatus 静默丢弃，故走 text 让两端都可见
                yield sse_event("text", {"content": HANDOVER_CONFIRM_TEXT})
                yield sse_event(
                    "done",
                    {
                        "message_id": str(user_msg.id),
                        "trace_id": collector.trace_id,
                        "takeover": True,
                        "session_status": SessionStatus.HANDOVER,
                    },
                )
                return

            history = await MessageRepo.list_recent(
                session.id, limit=effective_runtime(scene).history_limit, db=db
            )
            # 结束只读事务归还连接：LLM 流式生成期间不占用池内连接，
            # 后续写入时由 Session 惰性重新获取
            await db.commit()

            assistant_content = ""
            routed_to = None
            handover_triggered = False
            try:
                async for event in self.orchestrator.dispatch(
                    user_id=self._agent_subject(actor),
                    session_id=str(session.id),
                    query=message,
                    history=history,
                    user_message_id=str(user_msg.id),
                    # 上一轮承接的 Agent：供路由判定追问续接
                    last_route_to=session.last_route_to,
                    collector=collector,
                    scene=scene,
                ):
                    etype = event.get("type", "text")
                    data = event.get("data", event)
                    if etype == "route":
                        routed_to = data.get("agent_name")
                        # 直接改会话对象，随助手消息同一事务提交，省一次独立 UPDATE
                        session.last_route_to = routed_to
                    elif etype == "handover":
                        # 低置信/升级工具转人工：同事务置 handover，进坐席待接入队列；
                        # 结局改写为转人工（覆盖编排记的 answered），转人工率与全量落库都看这个字段
                        session.status = SessionStatus.HANDOVER
                        collector.record_outcome(OUTCOME_HANDOVER, reason="handover")
                        handover_triggered = True
                        break
                    if etype == "text":
                        piece = data.get("content", "") if isinstance(data, dict) else str(data)
                        # 先累积原文，整段落库前统一脱敏：逐 chunk 处理会漏掉被边界切开的号码
                        assistant_content += piece
                    yield sse_event(etype, data)
            except Exception as exc:  # noqa: BLE001
                # 客户端断开走 GeneratorExit，不落链路；真正的服务端异常必须留痕
                collector.record_outcome(OUTCOME_ERROR, message=str(exc))
                # 已生成的部分同样消耗了模型额度，归账时不能丢
                collector.record_completion(assistant_content)
                collector.mark_finished()
                await self._persist_trace(collector)
                await self._persist_usage(collector, integration_id, error=True)
                raise

            # 转人工：已流式下发的说明文本不落库（客户端自行渲染转人工提示），
            # 只提交会话状态改动，避免堆积空/重复的 assistant 行
            if handover_triggered:
                await db.commit()
                collector.mark_finished()
                await self._persist_usage(collector, integration_id)
                await self._persist_trace(collector)
                yield sse_event(
                    "done",
                    {
                        "message_id": str(user_msg.id),
                        "trace_id": collector.trace_id,
                        "takeover": None,
                        "session_status": SessionStatus.HANDOVER,
                    },
                )
                return

            assistant_content = sanitize_output(assistant_content)
            collector.response_chars = len(assistant_content)
            collector.record_completion(assistant_content)
            msg = await MessageRepo.add(
                session.id,
                role="assistant",
                content=assistant_content,
                routed_to=routed_to,
                db=db,
            )
            collector.assistant_message_id = str(msg.id)
            # 助手消息 + last_route_to 一并提交
            await db.commit()

        collector.mark_finished()
        # 先落用量与 trace 再下发 done：客户端收到 done 常立即断连，yield 之后的代码可能不再执行
        await self._persist_usage(collector, integration_id)
        await self._persist_trace(collector)
        yield sse_event("done", {"message_id": str(msg.id), "trace_id": collector.trace_id})

    async def reply(
        self,
        user_id: str | None,
        session_id: str | None,
        message: str,
        trace_id: str | None = None,
        scene_code: str | None = None,
        visitor_id: str | None = None,
        integration_id: str | None = None,
        scene: SceneContext | None = None,
    ) -> dict:
        """非流式备用接口：聚合完整回复"""
        if detect_injection(message):
            raise UnsafeInput()
        scene = scene or await resolve_scene(scene_code)
        actor = await self._resolve_actor(scene, user_id, visitor_id, integration_id)
        async with SessionLocal() as db:
            session = await SessionRepo.get_or_create_for_actor(
                actor, scene.scene_id, session_id, db=db
            )
            if session.title == DEFAULT_SESSION_TITLE:
                derived = derive_session_title(message)
                if derived:
                    session.title = derived
            user_msg = await MessageRepo.add(session.id, role="user", content=message, db=db)
            await db.commit()
            await self._attach_chat_event_identity(trace_id, session)

            history = await MessageRepo.list_recent(
                session.id, limit=effective_runtime(scene).history_limit, db=db
            )
            # 同 stream：读完成交释放连接，非流式生成期间也可能较长
            await db.commit()

            collector = TraceCollector(
                trace_id=trace_id or new_request_trace_id(),
                user_id=str(session.user_id) if session.user_id else None,
                session_id=str(session.id),
                scene_code=scene.code,
                visitor_id=str(session.visitor_id) if session.visitor_id else None,
            )
            collector.user_message_id = str(user_msg.id)

            # 与 stream 同一让位契约：接管/排队中只落库，不走编排、不产生机器人应答
            if session.status in (SessionStatus.HUMAN_HANDLING, SessionStatus.HANDOVER):
                takeover_status = session.status
                await SessionRepo.touch(session.id, db=db)
                collector.record_outcome(OUTCOME_HANDOVER, reason=takeover_status)
                collector.mark_finished()
                await self._persist_usage(collector, integration_id)
                await self._persist_trace(collector)
                return {
                    "session_id": str(session.id),
                    "message_id": str(user_msg.id),
                    "content": "",
                    "routed_to": None,
                    "trace_id": collector.trace_id,
                    "scene_code": scene.code,
                    "takeover": True,
                    "session_status": takeover_status,
                }

            # 与 stream 同一确定性转人工：明确要人工即置 handover，不走编排
            if session.status == SessionStatus.ACTIVE and _is_explicit_handover(message):
                session.status = SessionStatus.HANDOVER
                await db.commit()
                collector.record_outcome(OUTCOME_HANDOVER, reason="keyword")
                collector.mark_finished()
                await self._persist_usage(collector, integration_id)
                await self._persist_trace(collector)
                return {
                    "session_id": str(session.id),
                    "message_id": str(user_msg.id),
                    "content": HANDOVER_CONFIRM_TEXT,
                    "routed_to": None,
                    "trace_id": collector.trace_id,
                    "scene_code": scene.code,
                    "takeover": True,
                    "session_status": SessionStatus.HANDOVER,
                }

            content = ""
            routed_to = None
            handover_triggered = False
            try:
                async for event in self.orchestrator.dispatch(
                    user_id=self._agent_subject(actor),
                    session_id=str(session.id),
                    query=message,
                    history=history,
                    user_message_id=str(user_msg.id),
                    last_route_to=session.last_route_to,
                    collector=collector,
                    scene=scene,
                ):
                    etype = event.get("type", "text")
                    data = event.get("data", event)
                    if etype == "route":
                        routed_to = data.get("agent_name")
                        session.last_route_to = routed_to
                    elif etype == "handover":
                        session.status = SessionStatus.HANDOVER
                        collector.record_outcome(OUTCOME_HANDOVER, reason="handover")
                        handover_triggered = True
                        break
                    elif etype == "text":
                        content += data.get("content", "") if isinstance(data, dict) else str(data)
            except Exception as exc:  # noqa: BLE001
                collector.record_outcome(OUTCOME_ERROR, message=str(exc))
                collector.record_completion(content)
                collector.mark_finished()
                await self._persist_trace(collector)
                await self._persist_usage(collector, integration_id, error=True)
                raise

            # 转人工：与 stream 同样不落 assistant 行，只提交状态改动
            if handover_triggered:
                await db.commit()
                collector.mark_finished()
                await self._persist_usage(collector, integration_id)
                await self._persist_trace(collector)
                return {
                    "session_id": str(session.id),
                    "message_id": str(user_msg.id),
                    "content": "",
                    "routed_to": routed_to,
                    "trace_id": collector.trace_id,
                    "scene_code": scene.code,
                    "takeover": None,
                    "session_status": SessionStatus.HANDOVER,
                }

            content = sanitize_output(content)
            collector.response_chars = len(content)
            collector.record_completion(content)
            msg = await MessageRepo.add(
                session.id, role="assistant", content=content, routed_to=routed_to, db=db
            )
            collector.assistant_message_id = str(msg.id)
            await db.commit()

        collector.mark_finished()
        await self._persist_usage(collector, integration_id)
        await self._persist_trace(collector)
        return {
            "session_id": str(session.id),
            "message_id": str(msg.id),
            "content": content,
            "routed_to": routed_to,
            "trace_id": collector.trace_id,
            "scene_code": scene.code,
        }

    @staticmethod
    async def _resolve_actor(
        scene: SceneContext,
        user_id: str | None,
        visitor_id: str | None,
        integration_id: str | None = None,
    ) -> Actor:
        """登录客户优先；否则按外部访客标识（或新建匿名访客）签发 visitor actor。

        integration_id 来自 X-API-Key 反查：同一 external_key 在不同接入方下
        对应不同的 visitors 行，会话与记忆因此不会串号。
        """
        if user_id:
            return Actor(ActorType.USER, str(user_id))
        visitor = await VisitorRepo.get_or_create(
            scene.scene_id, external_key=visitor_id, integration_id=integration_id
        )
        return Actor(ActorType.VISITOR, str(visitor.id))

    @staticmethod
    def _agent_subject(actor: Actor) -> str:
        """传给 Agent/记忆层的隔离主体：客户沿用纯 UUID，访客用 visitor:<id> 避免撞库。"""
        return actor.id if actor.type is ActorType.USER else actor.key
