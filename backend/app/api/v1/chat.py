"""对话接口（SSE 流式 + 非流式备用）"""
import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import current_customer
from app.core.exceptions import UnsafeInput
from app.core.logging import new_trace_id
from app.core.security import detect_injection
from app.schemas.api import ChatRequest
from app.services.chat_service import ChatService
from app.utils.sse import SSE_HEADERS, sse_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    user=Depends(current_customer),
    service: ChatService = Depends(ChatService),
):
    if detect_injection(body.message):
        raise UnsafeInput()
    trace_id = new_trace_id()

    async def gen():
        try:
            async for chunk in service.stream(
                user_id=str(user.id),
                session_id=body.session_id,
                message=body.message,
                # 与 HTTP trace_id 对齐：后台链路页可直接用它在日志里 grep
                trace_id=trace_id,
            ):
                yield chunk
        except asyncio.CancelledError:
            # 客户端断开
            raise
        except Exception:
            # 内部异常细节（SQL/路径/堆栈）不能透给客户，只回泛化文案 + trace_id 供排查
            logger.exception(f"对话流处理异常 trace_id={trace_id}")
            yield sse_event(
                "error",
                {"code": 5001, "message": "服务暂时不可用，请稍后重试", "trace_id": trace_id},
            )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={**SSE_HEADERS, "X-Trace-Id": trace_id},
    )


@router.post("/message")
async def chat_message(
    body: ChatRequest,
    request: Request,
    user=Depends(current_customer),
    service: ChatService = Depends(ChatService),
):
    if detect_injection(body.message):
        raise UnsafeInput()
    trace_id = new_trace_id()
    result = await service.reply(
        user_id=str(user.id),
        session_id=body.session_id,
        message=body.message,
        trace_id=trace_id,
    )
    return {"code": 0, "message": "ok", "data": result, "trace_id": trace_id}
