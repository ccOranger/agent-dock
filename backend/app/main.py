"""FastAPI 应用入口"""
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
    allow_origins=["http://localhost:5173"],
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
