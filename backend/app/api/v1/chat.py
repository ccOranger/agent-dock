"""对话接口（SSE 流式 + 非流式备用）"""
import asyncio
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import optional_customer
from app.api.params import parse_uuid
from app.core.exceptions import BizError, NotFound, Unauthorized, UnsafeInput
from app.core.logging import new_trace_id
from app.core.ratelimit import ChatSlot, acquire_chat_slot
from app.core.security import detect_injection
from app.models.repositories import (
    ChatEventRepo,
    IntegrationRepo,
    MessageRepo,
    SessionRepo,
    VisitorRepo,
)
from app.schemas.api import ChatRequest
from app.schemas.common import ApiResponse
from app.schemas.scene import Actor, ActorType
from app.services.chat_service import ChatService
from app.services.scene_service import authorize_scene_access, resolve_scene
from app.utils.request import client_ip
from app.utils.sse import SSE_HEADERS, sse_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def _request_trace_id(request: Request) -> str:
    """沿用中间件已生成的 trace_id。

    端点自己再 new 一个会与响应头 `X-Trace-Id`（中间件写的）不一致：
    运维按头里的 id 查不到链路，评测执行器也无法按 trace_id 回读 RAG/token 事实。
    没有中间件（如精简测试应用）时才自行生成。
    """
    tid = getattr(request.state, "trace_id", None)
    return str(tid) if tid else new_trace_id()


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
    trace_id = _request_trace_id(request)
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
    trace_id = _request_trace_id(request)
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


@router.get("/poll", response_model=ApiResponse[dict])
async def chat_poll(
    request: Request,
    session_id: str = Query(..., description="待轮询会话 ID"),
    after_id: str | None = Query(None, description="增量游标：只返回该消息 ID 之后的新消息"),
    visitor_id: str | None = Query(None, description="匿名访客标识（登录客户可不传）"),
    scene_code: str | None = Query(None, max_length=32),
    user=Depends(optional_customer),
):
    """客户侧消息轮询：人工接管期间拉取坐席回复（接管无 SSE，靠前端定时轮询）。

    鉴权与 /chat/stream 同源：登录客户按 user_id 校验归属，匿名访客按
    visitor_id + publish_key/integration 解析出的内部访客行校验归属；
    归属不匹配一律 404，不泄露「该会话存在但不属于你」。不记量、不限流（只读）。
    """
    scene = await resolve_scene(scene_code)
    integration = await _resolve_and_authorize(request, scene, user)
    integration_id = str(integration["id"]) if integration else None

    if user is not None:
        actor = Actor(ActorType.USER, str(user.id))
    elif visitor_id:
        visitor = await VisitorRepo.get_or_create(
            scene.scene_id, external_key=visitor_id, integration_id=integration_id
        )
        actor = Actor(ActorType.VISITOR, str(visitor.id))
    else:
        raise Unauthorized("缺少客户身份或访客标识")

    sid = parse_uuid(session_id, "session_id")
    if sid is None:
        raise NotFound("会话不存在")
    sess = await SessionRepo.get(sid)
    if sess is None or not SessionRepo._belongs(sess, actor, scene.scene_id):
        raise NotFound("会话不存在")

    # after_id 非法时降级为全量：客户端游标丢失也不应 500，最多重传一次历史
    cursor = parse_uuid(after_id, "after_id") if after_id else None
    messages = await MessageRepo.list_by_session_after(sess.id, cursor)
    return ApiResponse(
        data={
            "session_id": str(sess.id),
            "status": sess.status,
            "messages": messages,
        }
    )
