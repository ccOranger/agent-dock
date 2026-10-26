"""对话接口（SSE 流式 + 非流式备用）"""
import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import optional_customer
from app.core.exceptions import BizError, Unauthorized, UnsafeInput
from app.core.logging import new_trace_id
from app.core.ratelimit import ChatSlot, acquire_chat_slot
from app.core.security import detect_injection
from app.models.repositories import ChatEventRepo, IntegrationRepo
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


def _scene_quota(scene):
    """场景总额度：未配置（0）时由限流层自行跳过这两个桶。"""
    runtime = getattr(scene, "runtime", None)
    return runtime.quota if runtime is not None else None


async def _resolve_and_authorize(request: Request, scene, user) -> dict | None:
    """按身份强度解析接入方：登录客户 > API Key > 嵌入公开标识。

    登录客户优先：带 customer token 时不再解析 Key 或公开标识；
    Key 无效时直接 401，公开标识无效时同样 401，都不降级为匿名，
    避免“本以为在按接入方隔离，实际串到匿名池”。
    """
    api_key = request.headers.get("x-api-key")
    integration = None
    if user is None and api_key:
        integration = await IntegrationRepo.resolve_key(api_key)
        if integration is None:
            raise Unauthorized("API key 无效")
    elif user is None:
        public_code = (request.headers.get("x-integration-code") or "").strip()
        if public_code:
            integration = await IntegrationRepo.resolve_public_code(public_code)
            if integration is None:
                raise Unauthorized("接入方公开标识无效")
    authorize_scene_access(
        scene,
        user=user,
        integration=integration,
        publish_key=request.headers.get("x-publish-key"),
    )
    return integration


async def _record_chat_event(
    *, trace_id: str, scene_code: str, body: ChatRequest, user, integration_id: str | None
) -> None:
    """对话埋点旁路：全量记录一次请求，失败只告警，绝不影响对话。"""
    try:
        await ChatEventRepo.record(
            trace_id=trace_id,
            scene_code=scene_code,
            session_id=body.session_id,
            visitor_id=body.visitor_id,
            user_id=str(user.id) if user is not None else None,
            integration_id=integration_id,
            ext=body.ext,
            message_chars=len(body.message),
        )
    except Exception as exc:  # noqa: BLE001 - 埋点不得影响主流程
        logger.warning(f"对话埋点写入失败（不阻塞对话）: {exc}")


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
    integration = await _resolve_and_authorize(request, scene, user)
    integration_id = str(integration["id"]) if integration else None
    trace_id = new_trace_id()
    await _record_chat_event(
        trace_id=trace_id,
        scene_code=scene.code,
        body=body,
        user=user,
        integration_id=integration_id,
    )
    slot: ChatSlot = await acquire_chat_slot(
        scene.code, _rate_actor(user, body, request), _scene_quota(scene)
    )

    async def gen():
        try:
            async for chunk in service.stream(
                user_id=str(user.id) if user is not None else None,
                session_id=body.session_id,
                message=body.message,
                # 与 HTTP trace_id 对齐：后台链路页可直接用它在日志里 grep
                trace_id=trace_id,
                visitor_id=body.visitor_id,
                integration_id=integration_id,
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
    integration = await _resolve_and_authorize(request, scene, user)
    integration_id = str(integration["id"]) if integration else None
    trace_id = new_trace_id()
    await _record_chat_event(
        trace_id=trace_id,
        scene_code=scene.code,
        body=body,
        user=user,
        integration_id=integration_id,
    )
    slot = await acquire_chat_slot(
        scene.code, _rate_actor(user, body, request), _scene_quota(scene)
    )
    try:
        result = await service.reply(
            user_id=str(user.id) if user is not None else None,
            session_id=body.session_id,
            message=body.message,
            trace_id=trace_id,
            visitor_id=body.visitor_id,
            integration_id=str(integration["id"]) if integration else None,
            scene=scene,
        )
        return {"code": 0, "message": "ok", "data": result, "trace_id": trace_id}
    finally:
        await slot.release()
