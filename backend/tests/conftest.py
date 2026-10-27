"""pytest 共享 fixture

- 若 hello-agents 未安装（如 CI 环境），注入最小桩模块，保证纯逻辑单测可运行
- 真实集成测试请安装 hello-agents 后运行
- 单测不应调用真实模型：无 .env 时注入占位 LLM 配置，避免 HelloAgentsLLM 构造即报错
"""
import os
import sys
import types
from types import SimpleNamespace

import pytest

os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_BASE_URL", "https://llm.invalid/v1")


def _install_hello_agents_stub() -> None:
    try:
        import hello_agents  # noqa: F401

        return
    except ImportError:
        pass

    pkg = types.ModuleType("hello_agents")
    llm_mod = types.ModuleType("hello_agents.llm")
    tools_mod = types.ModuleType("hello_agents.tools")
    agents_mod = types.ModuleType("hello_agents.agents")

    class _Stub:
        def __init__(self, *a, **kw):
            self.kwargs = kw

        async def run(self, *a, **kw):
            return ""

        async def stream_run(self, *a, **kw):
            if False:
                yield {}

    class _BaseTool(_Stub):
        name = "stub"
        description = "stub"
        parameters = {}

    class _ToolRegistry:
        def __init__(self, *a, **kw):
            self.tools = []

        def register_tool(self, tool):
            self.tools.append(tool)

    llm_mod.HelloAgentsLLM = type("HelloAgentsLLM", (_Stub,), {})
    tools_mod.MemoryTool = type("MemoryTool", (_BaseTool,), {})
    tools_mod.RAGTool = type("RAGTool", (_BaseTool,), {})
    tools_mod.MCPTool = type("MCPTool", (_BaseTool,), {})
    tools_mod.BaseTool = _BaseTool
    tools_mod.ToolRegistry = _ToolRegistry
    agents_mod.SimpleAgent = type("SimpleAgent", (_Stub,), {})

    pkg.llm = llm_mod
    pkg.tools = tools_mod
    pkg.agents = agents_mod
    sys.modules["hello_agents"] = pkg
    sys.modules["hello_agents.llm"] = llm_mod
    sys.modules["hello_agents.tools"] = tools_mod
    sys.modules["hello_agents.agents"] = agents_mod


_install_hello_agents_stub()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def make_test_app():
    """构造挂载指定路由的独立测试应用，并按角色覆盖 current_user 依赖（不触发真实 DB/启动事件）"""
    from fastapi import FastAPI

    from app.api.deps import current_user

    def _factory(routers: list, prefix: str = "/api/v1") -> FastAPI:
        app = FastAPI()
        for r in routers:
            app.include_router(r, prefix=prefix)
        return app

    def _as_role(app: FastAPI, role: str) -> FastAPI:
        user = SimpleNamespace(id="u-1", username=role, role=role, display_name=role, is_active=True)
        app.dependency_overrides[current_user] = lambda: user
        return app

    _factory.as_role = _as_role
    return _factory
