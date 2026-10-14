"""ChatService：编排对话流程并输出 SSE 事件流"""
import logging
from typing import AsyncIterator

from app.agents.orchestrator import Orchestrator
from app.core.exceptions import UnsafeInput
from app.core.security import detect_injection, sanitize_output
from app.core.tracing import OUTCOME_ERROR, TraceCollector, new_request_trace_id
from app.models.repositories import MessageRepo, SessionRepo, TraceRepo
from app.utils.sse import sse_event

logger = logging.getLogger(__name__)


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

    async def stream(
        self,
        user_id: str,
        session_id: str | None,
        message: str,
        trace_id: str | None = None,
    ) -> AsyncIterator[str]:
        session = await SessionRepo.get_or_create(user_id, session_id)
        user_msg = await MessageRepo.add(session.id, role="user", content=message)

        # 采集本轮经过的 Agent / MCP / 工具 / RAG / 记忆，流结束后一次写入 request_traces
        collector = TraceCollector(
            trace_id=trace_id or new_request_trace_id(),
            user_id=str(session.user_id),
            session_id=str(session.id),
        )
        collector.user_message_id = str(user_msg.id)

        yield sse_event("session", {"session_id": str(session.id)})

        history = await MessageRepo.list_by_session(session.id)

        assistant_content = ""
        routed_to = None
        try:
            async for event in self.orchestrator.dispatch(
                user_id=str(session.user_id),
                session_id=str(session.id),
                query=message,
                history=history,
                # 上一轮承接的 Agent：供路由判定追问续接
                last_route_to=session.last_route_to,
                collector=collector,
            ):
                etype = event.get("type", "text")
                data = event.get("data", event)
                if etype == "route":
                    routed_to = data.get("agent_name")
                    await SessionRepo.set_last_route_to(session.id, routed_to)
                if etype == "text":
                    piece = data.get("content", "") if isinstance(data, dict) else str(data)
                    assistant_content += sanitize_output(piece)
                yield sse_event(etype, data)
        except Exception as exc:  # noqa: BLE001
            # 客户端断开走 GeneratorExit，不落链路；真正的服务端异常必须留痕
            collector.record_outcome(OUTCOME_ERROR, message=str(exc))
            collector.mark_finished()
            await self._persist_trace(collector)
            raise

        collector.response_chars = len(assistant_content)
        msg = await MessageRepo.add(
            session.id,
            role="assistant",
            content=assistant_content,
            routed_to=routed_to,
        )
        collector.assistant_message_id = str(msg.id)
        collector.mark_finished()
        await self._persist_trace(collector)

        yield sse_event("done", {"message_id": str(msg.id), "trace_id": collector.trace_id})

    async def reply(
        self,
        user_id: str,
        session_id: str | None,
        message: str,
        trace_id: str | None = None,
    ) -> dict:
        """非流式备用接口：聚合完整回复"""
        if detect_injection(message):
            raise UnsafeInput()
        session = await SessionRepo.get_or_create(user_id, session_id)
        user_msg = await MessageRepo.add(session.id, role="user", content=message)
        history = await MessageRepo.list_by_session(session.id)

        collector = TraceCollector(
            trace_id=trace_id or new_request_trace_id(),
            user_id=str(session.user_id),
            session_id=str(session.id),
        )
        collector.user_message_id = str(user_msg.id)

        content = ""
        routed_to = None
        try:
            async for event in self.orchestrator.dispatch(
                user_id=str(session.user_id),
                session_id=str(session.id),
                query=message,
                history=history,
                last_route_to=session.last_route_to,
                collector=collector,
            ):
                etype = event.get("type", "text")
                data = event.get("data", event)
                if etype == "route":
                    routed_to = data.get("agent_name")
                    await SessionRepo.set_last_route_to(session.id, routed_to)
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
            session.id, role="assistant", content=content, routed_to=routed_to
        )
        collector.assistant_message_id = str(msg.id)
        collector.mark_finished()
        await self._persist_trace(collector)
        return {
            "session_id": str(session.id),
            "message_id": str(msg.id),
            "content": content,
            "routed_to": routed_to,
            "trace_id": collector.trace_id,
        }
