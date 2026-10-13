"""pytest 共享 fixture

- 若 hello-agents 未安装（如 CI 环境），注入最小桩模块，保证纯逻辑单测可运行
- 真实集成测试请安装 hello-agents 后运行
"""
import sys
import types

import pytest


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
