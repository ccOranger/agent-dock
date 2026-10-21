"""按场景来源校验的动态 CORS。

设计取舍：
- 全局白名单（settings.allowed_origins）继续放行自有前端；
- 第三方宿主页只有在其场景的 allowed_origins 里才放行，未配置来源的场景
  不会因为发布标识泄露就被任意站点嵌入；
- 每个请求都回库查场景会放大 DB 压力，这里做进程内 TTL 缓存，后台保存
  来源时主动失效；
- preflight 由中间件直接短路（204），不进入路由与鉴权，避免 OPTIONS
  被聊天接口的依赖链误伤。

场景 code 的解析顺序：/public/scenes/{code} 路径参数 → ?scene_code= →
X-Scene-Code 请求头 → 默认场景。聊天请求的 body 里虽然有 scene_code，
但中间件读 body 会破坏后续解析，因此约定嵌入组件同时带 query 或请求头。
"""
import logging
import re
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.models.repositories import SceneRepo
from app.schemas.scene import DEFAULT_SCENE_CODE

logger = logging.getLogger(__name__)

_SCENE_PATH_RE = re.compile(r"^/api/v1/public/scenes/([^/]+)")
_CORS_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
_DEFAULT_HEADERS = (
    "Authorization, Content-Type, X-API-Key, X-Publish-Key, "
    "X-Scene-Code, X-Trace-Id"
)

# scene_code -> (过期时刻 monotonic, 允许来源)
_origin_cache: dict[str, tuple[float, tuple[str, ...]]] = {}


def invalidate_origin_cache(scene_code: str) -> None:
    """后台改完来源配置后调用，下一次请求立刻读新值。"""
    _origin_cache.pop(scene_code, None)


def clear_origin_cache() -> None:
    _origin_cache.clear()


async def _scene_origins(scene_code: str) -> tuple[str, ...]:
    now = time.monotonic()
    cached = _origin_cache.get(scene_code)
    if cached is not None and cached[0] > now:
        return cached[1]
    scene = await SceneRepo.get_by_code(scene_code)
    origins = tuple(getattr(scene, "allowed_origins", None) or ()) if scene else ()
    ttl = max(0, settings.scene_origin_cache_ttl)
    _origin_cache[scene_code] = (now + ttl, origins)
    return origins


def _scene_code_from(request: Request) -> str:
    match = _SCENE_PATH_RE.match(request.url.path)
    if match:
        return match.group(1)
    raw = request.query_params.get("scene_code") or request.headers.get("x-scene-code")
    return (raw or "").strip() or DEFAULT_SCENE_CODE


class SceneCORSMiddleware(BaseHTTPMiddleware):
    """只对带 Origin 的跨域请求生效；同源请求零额外开销。"""

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        if not origin:
            return await call_next(request)

        preflight = request.method == "OPTIONS" and bool(
            request.headers.get("access-control-request-method")
        )
        allowed = await self._is_allowed(request, origin)
        if preflight:
            if not allowed:
                return JSONResponse(
                    status_code=403,
                    content={"code": 2003, "message": "来源未授权", "data": None},
                )
            return self._preflight_response(request, origin)

        response = await call_next(request)
        if allowed:
            _apply_headers(response, origin)
        return response

    async def _is_allowed(self, request: Request, origin: str) -> bool:
        if origin in settings.allowed_origin_list:
            return True
        # 只对业务接口按场景查来源；/health、/docs 等运维路径不查库
        if not request.url.path.startswith("/api/v1"):
            return False
        try:
            return origin in await _scene_origins(_scene_code_from(request))
        except Exception as exc:  # noqa: BLE001 - CORS 查库失败不应打死请求
            logger.warning("场景来源校验失败，按未授权处理: %s", exc)
            return False

    @staticmethod
    def _preflight_response(request: Request, origin: str):
        response = Response(status_code=204)
        _apply_headers(response, origin)
        requested_headers = request.headers.get("access-control-request-headers")
        response.headers["Access-Control-Allow-Headers"] = (
            requested_headers or _DEFAULT_HEADERS
        )
        response.headers["Access-Control-Allow-Methods"] = _CORS_METHODS
        response.headers["Access-Control-Max-Age"] = "600"
        return response


def _apply_headers(response, origin: str) -> None:
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Expose-Headers"] = "X-Trace-Id"
    # 来源随请求变化，缓存层必须按 Origin 分桶，否则会把 A 站的响应发给 B 站
    response.headers.add_vary_header("Origin")
