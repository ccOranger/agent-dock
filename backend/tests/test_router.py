"""路由 Agent 测试（需要 LLM 环境；CI 中可 mock）"""
import pytest

from app.agents.registry import AGENT_REGISTRY

VALID = {"knowledge_qa", "order_service", "complaint_handler"}


def test_registry_completeness():
    from app.packs.customer_service.agents.complaint_agent import ComplaintAgent
    from app.packs.customer_service.agents.knowledge_agent import KnowledgeAgent
    from app.packs.customer_service.agents.order_agent import OrderAgent

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


def test_prompt_carries_routing_clues():
    """路由线索（关键词/典型问法）要渲染成信息块注入，否则新 Agent 只有一行描述可猜。"""
    from app.agents.router_agent import RouterAgent

    prompt = RouterAgent()._render_prompt(
        [
            {
                "name": "invoice_service",
                "description": "开票服务",
                "routing_keywords": ["发票", "抬头", "税号"],
                "routing_examples": ["帮我开张发票", "发票抬头写错了能改吗"],
            }
        ],
        history=None,
        query="我要开票",
        last_route_to=None,
    )
    assert "关键词：发票、抬头、税号" in prompt
    assert "典型问法：帮我开张发票 / 发票抬头写错了能改吗" in prompt


def test_prompt_rules_not_hardcoded():
    """规则不得硬编码任何具名优先条款，否则后台新建的动态 Agent 抢不到路由。"""
    from app.agents.router_agent import RouterAgent

    prompt = RouterAgent()._build_prompt()
    for fragment in ("优先 order_service", "优先 complaint_handler", "若是一般知识问题"):
        assert fragment not in prompt
    # 兜底 Agent 已渲染为生效清单中的名称
    assert "agent_name 填 knowledge_qa" in prompt


def test_format_agent_list_degrades_without_clues():
    """未配线索的动态 Agent 降级为一行描述，不能渲染出空行或 “None”。"""
    from app.agents.router_agent import RouterAgent

    text = RouterAgent._format_agent_list([{"name": "bare_agent", "description": ""}])
    assert text == "- bare_agent：bare_agent"
    assert "关键词" not in text


def test_resolve_fallback_agent_from_effective_list():
    """兜底 Agent 从生效清单派生：knowledge_qa 被禁用时不能继续指向它。"""
    from app.agents.router_agent import RouterAgent

    both = [{"name": "order_service"}, {"name": "knowledge_qa"}]
    assert RouterAgent.resolve_fallback_agent(both) == "knowledge_qa"
    # knowledge_qa 不在清单（后台已禁用）→ 降级到首个生效 Agent
    assert RouterAgent.resolve_fallback_agent([{"name": "order_service"}]) == "order_service"
    # 清单为空 → 保持旧契约名称（反正下游会报未注册）
    assert RouterAgent.resolve_fallback_agent([]) == "knowledge_qa"


def test_parse_intents_fallback_uses_given_agent():
    """解析失败时落到传入的兜底 Agent，而非硬编码 knowledge_qa。"""
    from app.agents.router_agent import RouterAgent

    intents = RouterAgent._parse_intents("不按要求返回 JSON", {"invoice_service"}, "invoice_service")
    assert intents == [
        {"agent_name": "invoice_service", "confidence": 0.3, "reason": "解析失败", "is_fallback": True}
    ]


def test_fallback_intent_is_marked_so_it_survives_threshold():
    """兜底置信度（0.3）必然低于默认阈值，靠 is_fallback 标记才能与“模型真低置信”区分。"""
    from app.agents.router_agent import FALLBACK_CONFIDENCE, RouterAgent
    from app.core.config import settings

    assert FALLBACK_CONFIDENCE < settings.router_confidence_threshold
    intent = RouterAgent._parse_intents("乱码", VALID)[0]
    assert intent["is_fallback"] is True
    # 模型自己给出的正常意图不带该标记（即使置信度很低，仍该转人工）
    normal = RouterAgent._parse_intents(
        '{"intents":[{"agent_name":"knowledge_qa","confidence":0.2,"reason":"不确定"}]}', VALID
    )[0]
    assert "is_fallback" not in normal


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
    assert intents == [
        {"agent_name": "knowledge_qa", "confidence": 0.3, "reason": "解析失败", "is_fallback": True}
    ]


def test_knowledge_qa_claims_meta_questions():
    """“你是谁/你能做什么”这类元问题必须由 knowledge_qa 显式认领，
    否则路由会判“不贴合”走兜底，历史上这些提问会直接转人工。"""
    from app.packs.customer_service.agents.knowledge_agent import KnowledgeAgent

    for kw in ("你是谁", "能做什么"):
        assert kw in KnowledgeAgent.routing_keywords
    assert any("你是谁" in e for e in KnowledgeAgent.routing_examples)


def test_match_routing_clues_hits_substrings():
    """意图依据的命中词：子串匹配（不分词）、保留配置里的原词形，大小写不敏感。"""
    from app.agents.router_agent import match_routing_clues

    agent = {"name": "order_service", "routing_keywords": ["订单", "物流", "退款"]}
    assert match_routing_clues("我想查下订单，看看物流到哪了", agent) == ["订单", "物流"]
    # 命中判定不区分大小写，但回显用配置的写法（方便与后台表单对得上）
    assert match_routing_clues("有没有 FAQ 说明", {"routing_keywords": ["FAQ"]}) == ["FAQ"]
    assert match_routing_clues("", agent) == []
    assert match_routing_clues("随便说一句", None) == []
    assert match_routing_clues("随便说一句", {"routing_keywords": []}) == []


def test_match_routing_clues_is_capped():
    """命中词也设上限，避免关键词多的 Agent 把链路 span 撑大。"""
    from app.agents.router_agent import EVIDENCE_KEYWORDS_LIMIT, match_routing_clues

    words = [f"词{i}" for i in range(EVIDENCE_KEYWORDS_LIMIT + 4)]
    hits = match_routing_clues("".join(words), {"routing_keywords": words})
    assert len(hits) == EVIDENCE_KEYWORDS_LIMIT


def test_annotate_intent_evidence_three_states():
    """三种依据状态必须分清楚：命中 / 配了却没中 / 压根没配。"""
    from app.agents.router_agent import annotate_intent_evidence

    agents = [
        {"name": "order_service", "routing_keywords": ["订单", "物流"]},
        {"name": "invoice_service", "routing_keywords": ["发票"]},
        {"name": "refund_service", "routing_keywords": []},
    ]
    intents = [
        {"agent_name": "order_service", "confidence": 0.9, "reason": "查订单"},
        {"agent_name": "invoice_service", "confidence": 0.7, "reason": "开票"},
        {"agent_name": "refund_service", "confidence": 0.6, "reason": "退款"},
    ]
    annotate_intent_evidence(intents, agents, "帮我查下订单到哪了")
    assert (intents[0]["clue_basis"], intents[0]["matched_keywords"]) == ("keyword_hit", ["订单"])
    assert (intents[1]["clue_basis"], intents[1]["matched_keywords"]) == ("keyword_miss", [])
    assert (intents[2]["clue_basis"], intents[2]["matched_keywords"]) == ("no_keywords", [])


def test_annotate_intent_evidence_tolerates_unknown_agent():
    """意图指向清单里没的 Agent（理论上已被丢弃，防意外）也不能抛异常。"""
    from app.agents.router_agent import annotate_intent_evidence

    intents = [{"agent_name": "ghost", "confidence": 0.5, "reason": "r"}]
    annotate_intent_evidence(intents, [], "随便说一句")
    assert (intents[0]["clue_basis"], intents[0]["matched_keywords"]) == ("no_keywords", [])


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
