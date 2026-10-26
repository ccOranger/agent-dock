"""ChatService：编排对话流程并输出 SSE 事件流"""
import logging
from collections.abc import AsyncIterator

from app.agents.orchestrator import Orchestrator
from app.core.database import SessionLocal
from app.core.exceptions import UnsafeInput
from app.core.security import detect_injection, sanitize_output
from app.core.tracing import OUTCOME_ERROR, TraceCollector, new_request_trace_id
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

            history = await MessageRepo.list_recent(
                session.id, limit=effective_runtime(scene).history_limit, db=db
            )
            # 结束只读事务归还连接：LLM 流式生成期间不占用池内连接，
            # 后续写入时由 Session 惰性重新获取
            await db.commit()

            assistant_content = ""
            routed_to = None
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
                    if etype == "text":
                        piece = data.get("content", "") if isinstance(data, dict) else str(data)
                        # 先累积原文，整段落库前统一脱敏：逐 chunk 处理会漏掉被边界切开的号码
                        assistant_content += piece
                    yield sse_event(etype, data)
            except Exception as exc:  # noqa: BLE001
                # 客户端断开走 GeneratorExit，不落链路；真正的服务端异常必须留痕
                collector.record_outcome(OUTCOME_ERROR, message=str(exc))
                collector.mark_finished()
                await self._persist_trace(collector)
                raise

            assistant_content = sanitize_output(assistant_content)
            collector.response_chars = len(assistant_content)
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
        # 先落 trace 再下发 done：客户端收到 done 常立即断连，yield 之后的代码可能不再执行
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

            content = ""
            routed_to = None
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
                    elif etype == "text":
                        content += data.get("content", "") if isinstance(data, dict) else str(data)
            except Exception as exc:  # noqa: BLE001
                collector.record_outcome(OUTCOME_ERROR, message=str(exc))
                collector.mark_finished()
                await self._persist_trace(collector)
                raise

            content = sanitize_output(content)
            collector.response_chars = len(content)
            msg = await MessageRepo.add(
                session.id, role="assistant", content=content, routed_to=routed_to, db=db
            )
            collector.assistant_message_id = str(msg.id)
            await db.commit()

        collector.mark_finished()
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
