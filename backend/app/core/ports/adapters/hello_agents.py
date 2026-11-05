"""hello-agents 后端适配器：**全仓库唯一允许 import hello_agents 的运行时模块**。

除本文件外，``app/`` 下的代码只允许 import ``app.core.ports``。这样在 P2–P5 替换实现时，
改动面被限制在本文件与 ``ports/protocols.py``。

适配器原则：

1. **透明代理**：``__getattr__`` 兜底转发，任何没显式实现的方法/属性都直达上游对象，
   保证「包一层」不改变行为（这是 P1 的验收前提：逐字节一致的 SSE 序列）；
2. **保留 ``.raw``**：上游对象之间互相引用（``SimpleAgent(llm=...)``、``register_tool(tool)``）
   时通过 ``.raw`` 拿回原始实例，避免适配器渗进框架内部；
3. **不做字段转换**：不重排参数、不改写返回值、不吞异常。上游的返回是格式化字符串
   （``"📊 分块数量: 12"``），下游有代码在解析它，P1 阶段必须原样透传。
"""
from __future__ import annotations

from typing import Any

from hello_agents import HelloAgentsLLM, Message
from hello_agents.agents import SimpleAgent
from hello_agents.protocols.mcp.client import MCPClient
from hello_agents.tools import MCPTool, MemoryTool, RAGTool, Tool, ToolParameter, ToolRegistry

# ============================================================================
# 过渡期再导出（P2–P5 逐个替换为自研实现）
#
# 下面这些**不是**适配器，只是把「谁 import 了上游」这一事实收敛到本文件：
# app/ 下其余模块一律从本模块取名字，将来替换实现时只改这里。
# 每替换掉一个符号，就在 docs/hello-agents-removal-plan.md 里划掉一项。
# ============================================================================

__all__ = [
    "HelloAgentsLLMAdapter",
    "HelloAgentsToolAdapter",
    "MCPClient",
    "MCPTool",
    "Message",
    "SimpleAgent",
    "Tool",
    "ToolParameter",
    "ToolRegistry",
    "create_llm",
    "create_memory_config",
    "create_memory_tool",
    "create_rag_tool",
]


class HelloAgentsLLMAdapter:
    """``LLMPort`` 的 hello-agents 实现。"""

    __slots__ = ("_raw",)

    def __init__(self, raw: HelloAgentsLLM) -> None:
        self._raw = raw

    @property
    def raw(self) -> HelloAgentsLLM:
        """上游实例，供 ``SimpleAgent(llm=...)`` 这类框架内部互引用使用。"""
        return object.__getattribute__(self, "_raw")

    @property
    def model(self) -> str | None:
        return getattr(self.raw, "model", None)

    def invoke(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        return self.raw.invoke(messages, **kwargs)

    def stream_invoke(self, messages: list[dict[str, str]], **kwargs: Any):
        return self.raw.stream_invoke(messages, **kwargs)

    def __getattr__(self, item: str) -> Any:
        # 只在常规查找失败后触发；_raw 尚未就绪时用 object.__getattribute__ 取，避免递归
        return getattr(object.__getattribute__(self, "_raw"), item)

    def __repr__(self) -> str:
        return f"<HelloAgentsLLMAdapter model={self.model!r}>"


class HelloAgentsToolAdapter:
    """``ToolPort`` 的 hello-agents 实现，同时覆盖 Memory / RAG / MCP。

    上游的 ``ToolRegistry.register_tool`` 是鸭子类型（读 ``name``/``expandable``/
    ``get_expanded_tools``），所以代理对象可以直接注册；注册后 Agent 调 ``run(dict)``
    也由这里转发，行为与直接注册原始对象一致。
    """

    __slots__ = ("_raw",)

    def __init__(self, raw: Tool) -> None:
        self._raw = raw

    @property
    def raw(self) -> Tool:
        return object.__getattribute__(self, "_raw")

    @property
    def name(self) -> str:
        return self.raw.name

    @property
    def description(self) -> str:
        return self.raw.description

    @property
    def expandable(self) -> bool:
        # 上游只有部分工具（MCP）定义了该属性；不存在时等价于 False
        return bool(getattr(self.raw, "expandable", False))

    def run(self, *args: Any, **kwargs: Any) -> Any:
        return self.raw.run(*args, **kwargs)

    def get_parameters(self) -> list[Any]:
        return self.raw.get_parameters()

    def to_dict(self) -> dict[str, Any]:
        return self.raw.to_dict()

    def get_expanded_tools(self) -> list[Any]:
        return self.raw.get_expanded_tools()

    def __getattr__(self, item: str) -> Any:
        return getattr(object.__getattribute__(self, "_raw"), item)

    def __repr__(self) -> str:
        return f"<HelloAgentsToolAdapter name={self.name!r}>"


def create_llm(model: str | None = None) -> HelloAgentsLLMAdapter:
    """按模型名创建 LLM 客户端（不缓存，缓存由调用方负责）。"""
    return HelloAgentsLLMAdapter(HelloAgentsLLM(model=model))


def create_memory_tool(*, user_id: str, memory_config: Any, memory_types: list[str]) -> HelloAgentsToolAdapter:
    """创建记忆工具；参数与上游 ``MemoryTool`` 一一对应，不做加工。"""
    return HelloAgentsToolAdapter(
        MemoryTool(user_id=user_id, memory_config=memory_config, memory_types=memory_types)
    )


def create_rag_tool(*, knowledge_base_path: str, rag_namespace: str) -> HelloAgentsToolAdapter:
    """创建知识库工具；参数与上游 ``RAGTool`` 一一对应。"""
    return HelloAgentsToolAdapter(
        RAGTool(knowledge_base_path=knowledge_base_path, rag_namespace=rag_namespace)
    )


def create_memory_config(*, working_memory_ttl_minutes: int) -> Any:
    """构造上游 ``MemoryConfig``。返回原始对象（它是纯配置值，无行为、无需代理）。"""
    from hello_agents.memory.base import MemoryConfig

    return MemoryConfig(working_memory_ttl_minutes=working_memory_ttl_minutes)
