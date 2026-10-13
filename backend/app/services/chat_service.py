"""ChatService：编排对话流程并输出 SSE 事件流"""
import logging
from typing import AsyncIterator

from app.agents.orchestrator import Orchestrator
from app.core.exceptions import UnsafeInput
from app.core.security import detect_injection, sanitize_output
from app.models.repositories import MessageRepo, SessionRepo
from app.utils.sse import sse_event

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self):
        self.orchestrator = Orchestrator()

    async def stream(
        self,
        user_id: str,
        session_id: str | None,
        message: str,
    ) -> AsyncIterator[str]:
        session = await SessionRepo.get_or_create(user_id, session_id)
        await MessageRepo.add(session.id, role="user", content=message)

        yield sse_event("session", {"session_id": str(session.id)})

        history = await MessageRepo.list_by_session(session.id)

        assistant_content = ""
        routed_to = None
        async for event in self.orchestrator.dispatch(
            user_id=str(session.user_id),
            session_id=str(session.id),
            query=message,
            history=history,
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

        msg = await MessageRepo.add(
            session.id,
            role="assistant",
            content=assistant_content,
            routed_to=routed_to,
        )

        yield sse_event("done", {"message_id": str(msg.id)})

    async def reply(self, user_id: str, session_id: str | None, message: str) -> dict:
        """非流式备用接口：聚合完整回复"""
        if detect_injection(message):
            raise UnsafeInput()
        session = await SessionRepo.get_or_create(user_id, session_id)
        await MessageRepo.add(session.id, role="user", content=message)
        history = await MessageRepo.list_by_session(session.id)

        content = ""
        routed_to = None
        async for event in self.orchestrator.dispatch(
            user_id=str(session.user_id),
            session_id=str(session.id),
            query=message,
            history=history,
        ):
            etype = event.get("type", "text")
            data = event.get("data", event)
            if etype == "route":
                routed_to = data.get("agent_name")
            elif etype == "text":
                content += data.get("content", "") if isinstance(data, dict) else str(data)

        content = sanitize_output(content)
        msg = await MessageRepo.add(
            session.id, role="assistant", content=content, routed_to=routed_to
        )
        return {"session_id": str(session.id), "message_id": str(msg.id), "content": content, "routed_to": routed_to}
