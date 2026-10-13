"""路由 Agent 测试（需要 LLM 环境；CI 中可 mock）"""
import pytest

from app.agents.registry import AGENT_REGISTRY


def test_registry_completeness():
    from app.agents.business.knowledge_agent import KnowledgeAgent
    from app.agents.business.order_agent import OrderAgent
    from app.agents.business.complaint_agent import ComplaintAgent

    assert KnowledgeAgent.name in AGENT_REGISTRY
    assert OrderAgent.name in AGENT_REGISTRY
    assert ComplaintAgent.name in AGENT_REGISTRY


def test_new_agent_auto_discovered():
    """新增 Agent 后，路由 prompt 应包含它"""
    from app.agents.router_agent import RouterAgent

    router = RouterAgent()
    prompt = router._build_prompt()
    for name, cls in AGENT_REGISTRY.items():
        assert name in prompt
        assert cls.description in prompt


@pytest.mark.skip(reason="需要真实 LLM API Key")
async def test_router_order_intent():
    from app.agents.router_agent import RouterAgent

    router = RouterAgent()
    result = await router.route("我的订单到哪了")
    assert result["agent_name"] == "order_service"
    assert result["confidence"] > 0.5
