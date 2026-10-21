"""对话接口（SSE 流式 + 非流式备用）"""
import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import optional_customer
from app.core.exceptions import BizError, UnsafeInput
from app.core.logging import new_trace_id
from app.core.ratelimit import ChatSlot, acquire_chat_slot
from app.core.security import detect_injection
from app.schemas.api import ChatRequest
from app.services.chat_service import ChatService
from app.services.scene_service import authorize_scene_access, resolve_scene
from app.utils.request import client_ip
from app.utils.sse import SSE_HEADERS, sse_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def _rate_actor(user, body: ChatRequest, request: Request) -> str:
    """限流主体：登录客户按 user，访客按 visitor_id，其余按 IP。"""
    if user is not None:
        return f"user:{user.id}"
    if body.visitor_id:
        return f"visitor:{body.visitor_id}"
    return f"ip:{client_ip(request)}"


def _authorize(request: Request, scene, user) -> None:
    authorize_scene_access(
        scene,
        user=user,
        api_key=request.headers.get("x-api-key"),
        publish_key=request.headers.get("x-publish-key"),
    )


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    user=Depends(optional_customer),
    service: ChatService = Depends(ChatService),
):
    if detect_injection(body.message):
        raise UnsafeInput()
    # 场景错误（3003/3004/3005）在流开始前返回，客户端才能拿到正确 HTTP 状态码
    scene = await resolve_scene(body.scene_code)
    _authorize(request, scene, user)
    slot: ChatSlot = await acquire_chat_slot(scene.code, _rate_actor(user, body, request))
    trace_id = new_trace_id()

    async def gen():
        try:
            async for chunk in service.stream(
                user_id=str(user.id) if user is not None else None,
                session_id=body.session_id,
                message=body.message,
                # 与 HTTP trace_id 对齐：后台链路页可直接用它在日志里 grep
                trace_id=trace_id,
                visitor_id=body.visitor_id,
                scene=scene,
            ):
                yield chunk
        except asyncio.CancelledError:
            # 客户端断开
            raise
        except BizError as exc:
            # 业务异常透传原错误码（如输入不安全 4003），不要模糊成 5001
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            logger.warning(f"对话流业务异常 trace_id={trace_id} code={detail.get('code')}")
            yield sse_event(
                "error",
                {
                    "code": detail.get("code", 5001),
                    "message": detail.get("message") or "请求失败",
                    "trace_id": trace_id,
                },
            )
        except Exception:
            # 内部异常细节（SQL/路径/堆栈）不能透给客户，只回泛化文案 + trace_id 供排查
            logger.exception(f"对话流处理异常 trace_id={trace_id}")
            yield sse_event(
                "error",
                {"code": 5001, "message": "服务暂时不可用，请稍后重试", "trace_id": trace_id},
            )
        finally:
            await slot.release()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={**SSE_HEADERS, "X-Trace-Id": trace_id},
    )


@router.post("/message")
async def chat_message(
    body: ChatRequest,
    request: Request,
    user=Depends(optional_customer),
    service: ChatService = Depends(ChatService),
):
    if detect_injection(body.message):
        raise UnsafeInput()
    scene = await resolve_scene(body.scene_code)
    _authorize(request, scene, user)
    slot = await acquire_chat_slot(scene.code, _rate_actor(user, body, request))
    try:
        trace_id = new_trace_id()
        result = await service.reply(
            user_id=str(user.id) if user is not None else None,
            session_id=body.session_id,
            message=body.message,
            trace_id=trace_id,
            visitor_id=body.visitor_id,
            scene=scene,
        )
        return {"code": 0, "message": "ok", "data": result, "trace_id": trace_id}
    finally:
        await slot.release()
