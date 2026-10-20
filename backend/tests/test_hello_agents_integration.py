"""HelloAgents 全栈集成测试（需真实依赖、LLM Key 与嵌入模型；conftest 桩环境下自动跳过）

默认跳过：需要真实模型调用（产生费用且依赖网络）。单独验证时：
    RUN_HELLO_AGENTS_INTEGRATION=1 pytest tests/test_hello_agents_integration.py
"""
import os
import sys

import pytest

if "hello_agents" not in sys.modules or not hasattr(sys.modules["hello_agents"], "__file__"):
    pytest.skip("hello-agents 未安装，跳过集成测试", allow_module_level=True)

if os.getenv("RUN_HELLO_AGENTS_INTEGRATION", "").strip().lower() not in {"1", "true", "yes", "on"}:
    pytest.skip("未设置 RUN_HELLO_AGENTS_INTEGRATION=1，跳过需要真实模型的全栈集成测试", allow_module_level=True)


@pytest.mark.asyncio
async def test_full_hello_agents_stack():
    from hello_agents import HelloAgentsLLM
    from hello_agents.agents import SimpleAgent
    from hello_agents.tools import MemoryTool, RAGTool, ToolRegistry

    from app.core.config import settings

    memory_tool = MemoryTool(
        user_id="test_user",
        memory_types=settings.memory_type_list,
    )
    rag_tool = RAGTool(
        knowledge_base_path="./tests/fixtures/kb",
        rag_namespace="test_ns",
    )

    registry = ToolRegistry()
    registry.register_tool(memory_tool)
    registry.register_tool(rag_tool)

    llm = HelloAgentsLLM()
    agent = SimpleAgent(
        name="测试助手",
        llm=llm,
        system_prompt="测试用智能助手",
        tool_registry=registry,
    )

    memory_tool.run(
        {
            "action": "add",
            "memory_type": "episodic",
            "content": "测试记忆",
            "metadata": {},
        }
    )
    result = memory_tool.run({"action": "search", "query": "测试", "limit": 5})
    assert result is not None

    # SimpleAgent.run 为同步 API，直接调用
    output = agent.run("你好")
    assert output
