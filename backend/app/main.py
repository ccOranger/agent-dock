"""FastAPI 应用入口"""
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_client import generate_latest

from app.api.v1.router import api_router
from app.core.bootstrap import bootstrap, init_hello_agents, validate_agents_sync
from app.core.config import settings, validate_runtime_config
from app.core.constants import API_PREFIX
from app.core.context_engineer import drain_background_tasks
from app.core.cors import SceneCORSMiddleware
from app.core.license import log_startup_license
from app.core.logging import new_trace_id, setup_logging
from app.core.ratelimit import close_redis
from app.core.schema import assert_schema_version
from app.core.selfcheck import run_startup_selfcheck
from app.services.scene_service import ensure_default_scene

logger = logging.getLogger(__name__)

# Windows 控制台默认 GBK 编码，而 hello_agents 的 MCP 客户端用裸 print 输出 emoji
# （📝/🔗/✅ 等），会抛 UnicodeEncodeError 并被工具发现逻辑静默吞掉，导致 MCP 工具列表为空。
# 统一把标准输出/错误切到 UTF-8（无法编码的字符以 replace 兜底），保证 MCP 注册不受影响。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_config()
    # 授权只降级不阻断：先输出一行版本与到期天数，再继续启动
    log_startup_license()
    await assert_schema_version()
    await run_startup_selfcheck()
    init_hello_agents()
    await validate_agents_sync()
    # 默认场景兜底：初始化脚本已种；这里处理被误删导致的“无码请求无落点”
    await ensure_default_scene()
    yield
    # 先排空后台记忆归档再关资源：否则热重启/滚动发布时正在归档的轮次会被取消丢弃
    try:
        await drain_background_tasks()
    finally:
        try:
            await close_redis()
        finally:
            bootstrap.shutdown()


setup_logging(settings.log_level)

app = FastAPI(
    title="smart-customer-service",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(SceneCORSMiddleware)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # 未捕获异常必须留堆栈：否则线上只能看到一句“内部错误”，无从定位
    logger.error(
        f"未处理异常: {request.method} {request.url.path} trace_id={getattr(request.state, 'trace_id', '-')}",
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={"code": 5001, "message": "内部错误", "data": None},
    )


app.include_router(api_router, prefix=API_PREFIX)


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "app": settings.app_name}


@app.get("/metrics", tags=["ops"])
async def metrics():
    from fastapi.responses import Response

    return Response(generate_latest(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=True)
