"""六个能力端口的协议定义（纯类型，无运行时依赖）。

这些协议描述的是**项目实际用到的调用形状**，不是上游框架的完整 API：

- LLM：``invoke``（同步返回文本）与 ``stream_invoke``（同步产出文本增量）；
- Tool：``name`` / ``description`` / ``run``。Memory、RAG、MCP 都是 Tool 的特化，
  因为上游把三者都实现成可注册的工具，项目也是按工具用它们的（``tool.run({...})``）；
- Agent：``add_message`` / ``run`` / ``stream_run`` / ``llm`` / ``_history`` 的可见化
  （上游用私有 ``_history``，项目已在 orchestrator 里直接读写，这里把它提升为协议成员，
  P3 换实现时才有稳定的对接面）；
- Registry：``register_tool`` / ``get_tool``。

刻意不做的抽象：不定义"消息模型""工具参数模型"等 Pydantic 结构。项目在 P1 阶段
必须保持与上游逐字节一致的行为，任何自造模型都会改变序列化结果（见计划文档 §5）。
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol


class LLMPort(Protocol):
    """对话模型客户端。

    ``invoke`` 与 ``stream_invoke`` 都接受 OpenAI 风格的 ``messages``
    （``[{"role": ..., "content": ...}]``），与上游签名保持一致。
    """

    model: str | None

    @property
    def raw(self) -> Any:
        """回到后端原生对象。

        只为一处互操作保留：``SimpleAgent(llm=...)`` 要求上游 ``HelloAgentsLLM`` 实例。
        自研实现（P2 之后）把 ``raw`` 指向自身即可，调用方无需改动。
        """
        ...

    def invoke(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """整段返回；上游等价物为 ``HelloAgentsLLM.invoke``。"""
        ...

    def stream_invoke(self, messages: list[dict[str, str]], **kwargs: Any) -> Iterator[str]:
        """流式返回文本增量；上游等价物为 ``HelloAgentsLLM.stream_invoke``。"""
        ...


class ToolPort(Protocol):
    """可注册到 Registry、可被 Agent 调用的一次性能力。"""

    name: str
    description: str

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """执行工具。项目现有调用有两种形状：``run(params: dict)``（框架约定）
        与 ``run(**kwargs)``（异步工具被 to_thread 包裹时）。协议只约束"可调用"。"""
        ...


class MemoryPort(ToolPort, Protocol):
    """四层记忆工具（working / episodic / perceptual / semantic）。

    项目只经由 ``run({"action": ...})`` 使用它，动作集合由上游定义；
    P4 换实现时本协议不变，只是 action 的语义由自研实现承担。
    """


class RAGPort(ToolPort, Protocol):
    """知识库工具（入库 / 检索 / 统计 / 重建索引）。"""


class MCPPort(ToolPort, Protocol):
    """MCP 客户端工具：``expandable`` 的复合工具，展开后每个远端工具独立注册。"""

    expandable: bool


class AgentPort(Protocol):
    """对话 Agent：流式生成 + 工具循环由实现负责，项目只负责调度。"""

    name: str
    llm: LLMPort

    def add_message(self, message: Any) -> None:
        """向会话历史追加一条消息（上游 ``Message``）。"""
        ...

    def run(self, text: str) -> str:
        """一次性返回（同步阻塞，调用方用线程池包裹）。"""
        ...

    def stream_run(self, text: str) -> Iterator[str]:
        """同步生成器，产出文本增量。"""
        ...


class ToolRegistryPort(Protocol):
    """工具注册表：注册 + 按名取用。"""

    def register_tool(self, tool: Any, auto_expand: bool = True) -> None:
        ...

    def get_tool(self, name: str) -> Any | None:
        ...
