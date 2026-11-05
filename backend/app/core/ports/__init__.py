"""能力端口（Port）：项目对 Agent 框架的全部依赖只允许经由这里。

设计约束（见 docs/hello-agents-removal-plan.md §2）：

1. 本包**只允许**出现标准库与 typing 依赖，任何第三方/框架导入都视为破坏分层；
2. 协议按"项目实际用到的形状"定义，不做通用化设计——多一个方法就多一份实现成本；
3. 实现放在 ``app/core/ports/adapters/``，每个后端一个模块；切换后端只换适配器，
   调用方（orchestrator / services / api）不感知。
4. P1 阶段适配器保留 ``.raw`` 透传原始对象：框架内部互相引用（SimpleAgent 需要
   HelloAgentsLLM 实例、ToolRegistry 需要 Tool 实例）时仍能互操作，保证行为零变化。
"""

from app.core.ports.protocols import (
    AgentPort,
    LLMPort,
    MCPPort,
    MemoryPort,
    RAGPort,
    ToolPort,
    ToolRegistryPort,
)

__all__ = [
    "AgentPort",
    "LLMPort",
    "MCPPort",
    "MemoryPort",
    "RAGPort",
    "ToolPort",
    "ToolRegistryPort",
]
