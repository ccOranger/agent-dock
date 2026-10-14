"""FastAPI 应用入口"""
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import generate_latest

from app.api.v1.router import api_router
from app.core.bootstrap import bootstrap, init_hello_agents, validate_agents_sync
from app.core.config import settings
from app.core.constants import API_PREFIX
from app.core.logging import new_trace_id, setup_logging

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
    init_hello_agents()
    await validate_agents_sync()
    yield
    bootstrap.shutdown()


setup_logging(settings.log_level)

app = FastAPI(
    title="smart-customer-service",
    version="1.3.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
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
