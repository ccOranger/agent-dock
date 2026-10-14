"""路由 Agent 测试（需要 LLM 环境；CI 中可 mock）"""
import pytest

from app.agents.registry import AGENT_REGISTRY

VALID = {"knowledge_qa", "order_service", "complaint_handler"}


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


def test_parse_intents_multi_keeps_order():
    from app.agents.router_agent import RouterAgent

    raw = (
        '{"intents":['
        '{"agent_name":"order_service","confidence":0.9,"reason":"查订单"},'
        '{"agent_name":"knowledge_qa","confidence":0.7,"reason":"问政策"}]}'
    )
    intents = RouterAgent._parse_intents(raw, VALID)
    # 保持模型给出的主次顺序，不按置信度重排
    assert [i["agent_name"] for i in intents] == ["order_service", "knowledge_qa"]
    assert intents[1]["confidence"] == 0.7


def test_parse_intents_compatible_with_legacy_single_format():
    """旧单意图输出仍被接受（归一化为 1 个意图）"""
    from app.agents.router_agent import RouterAgent

    raw = '{"agent_name":"complaint_handler","confidence":0.85,"reason":"投诉"}'
    intents = RouterAgent._parse_intents(raw, VALID)
    assert len(intents) == 1
    assert intents[0]["agent_name"] == "complaint_handler"


def test_parse_intents_dedupe_and_drop_unknown():
    from app.agents.router_agent import RouterAgent

    raw = (
        '{"intents":['
        '{"agent_name":"order_service","confidence":0.9,"reason":"a"},'
        '{"agent_name":"ghost_agent","confidence":0.9,"reason":"b"},'
        '{"agent_name":"order_service","confidence":0.6,"reason":"c"}]}'
    )
    assert [i["agent_name"] for i in RouterAgent._parse_intents(raw, VALID)] == ["order_service"]


@pytest.mark.parametrize(
    "raw",
    [
        "模型没有按 JSON 返回",
        '{"intents":[{"agent_name":"ghost_agent","confidence":0.9,"reason":"臆造"}]}',
    ],
)
def test_parse_intents_fallback_to_knowledge_qa(raw):
    from app.agents.router_agent import RouterAgent

    intents = RouterAgent._parse_intents(raw, VALID)
    assert intents == [{"agent_name": "knowledge_qa", "confidence": 0.3, "reason": "解析失败"}]


def test_parse_intents_tolerates_code_fence():
    from app.agents.router_agent import RouterAgent

    raw = '```json\n{"intents":[{"agent_name":"knowledge_qa","confidence":0.6,"reason":"faq"}]}\n```'
    intents = RouterAgent._parse_intents(raw, VALID)
    assert intents[0]["agent_name"] == "knowledge_qa"


def test_conversation_excludes_current_query():
    """history 末尾与当前消息重复的客户轮应被剔除，并保留上一轮客服回答"""
    from app.agents.router_agent import RouterAgent

    history = [
        {"role": "user", "content": "我的订单 ORD123456 到哪了"},
        {"role": "assistant", "content": "已发往上海转运中心"},
        {"role": "user", "content": "那什么时候能到"},
    ]
    text = RouterAgent._format_conversation(history, "那什么时候能到")
    assert "已发往上海转运中心" in text
    assert text.count("那什么时候能到") == 0
    assert RouterAgent._format_conversation([], "你好") == "（无）"


def test_render_prompt_carries_history_and_last_agent():
    """追问续接所需的上下文必须进入路由提示词"""
    from app.agents.registry import list_agents
    from app.agents.router_agent import RouterAgent

    router = RouterAgent()
    prompt = router._render_prompt(
        list_agents(),
        history=[{"role": "assistant", "content": "已发往上海转运中心"}],
        query="那什么时候能到",
        last_route_to="order_service",
    )
    assert "intents" in prompt
    assert "已发往上海转运中心" in prompt
    assert "order_service" in prompt


@pytest.mark.skip(reason="需要真实 LLM API Key")
async def test_router_order_intent():
    from app.agents.router_agent import RouterAgent

    router = RouterAgent()
    result = await router.route("我的订单到哪了")
    assert result["agent_name"] == "order_service"
    assert result["confidence"] > 0.5
    assert result["intents"][0]["agent_name"] == "order_service"
