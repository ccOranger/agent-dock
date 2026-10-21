"""M3 场景路由档案：0/1/N 候选、继承/覆盖、兜底降级、警告与 prompt_hash 稳定性。

全部为纯函数/无 DB 测试：档案构建不访问数据库，仓库查询由 scene_service 负责。
"""
import json
import uuid

import pytest

from app.agents import router_agent as ra
from app.agents.scene_router import (
    FALLBACK_SOURCE_CONFIGURED_MISSING,
    FALLBACK_SOURCE_POOL_FIRST,
    FALLBACK_SOURCE_PREFERRED,
    FALLBACK_SOURCE_SCENE,
    build_scene_router_profile,
    profile_to_dict,
)
from app.core.config import settings
from app.schemas.scene import (
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_FALLBACK_TEXT,
    DEFAULT_SCENE_ID,
    SceneContext,
    SceneWarningCode,
)


def _candidate(name: str, *, description: str | None = None, keywords=None, examples=None, **kw) -> dict:
    return {
        "id": kw.get("id") or str(uuid.uuid4()),
        "name": name,
        "display_name": name,
        "description": description or f"{name} 的适用范围",
        "source": "dynamic",
        "scene_id": kw.get("scene_id", "scene-1"),
        "routing_keywords": list(keywords or []),
        "routing_examples": list(examples or []),
        "sort_order": kw.get("sort_order", 0),
    }


def _context(candidates=(), **kw) -> SceneContext:
    candidates = tuple(candidates)
    base = {
        "scene_id": DEFAULT_SCENE_ID,
        "code": "mall_order",
        "is_default": False,
        "router_mode": "auto",
        "agent_names": tuple(c["name"] for c in candidates),
        "direct_agent": None,
        "name": "商城订单",
        "status": "active",
        "router_model": None,
        "router_confidence_threshold": None,
        "router_max_intents": None,
        "fallback_agent_id": None,
        "fallback_text": None,
        "extra_router_prompt": None,
        "candidates": candidates,
    }
    base.update(kw)
    return SceneContext(**base)


# ===== 0 / 1 / N 候选 =====
def test_zero_agents_is_unconfigured():
    profile = build_scene_router_profile(_context())
    assert profile.mode == "unconfigured"
    assert profile.direct_agent is None
    assert profile.fallback_agent is None
    assert profile.fallback_source == "empty"
    assert SceneWarningCode.NO_AGENTS.value in profile.warnings
    assert len(profile.prompt_hash) == 12


def test_single_agent_auto_is_direct():
    profile = build_scene_router_profile(_context([_candidate("order_service")]))
    assert profile.mode == "direct"
    assert profile.configured_mode == "auto"
    assert profile.direct_agent == "order_service"
    assert profile.fallback_agent == "order_service"
    assert profile.fallback_source == FALLBACK_SOURCE_POOL_FIRST
    assert SceneWarningCode.SINGLE_AGENT_DIRECT.value in profile.warnings


def test_multi_agent_auto_uses_llm():
    profile = build_scene_router_profile(
        _context([_candidate("order_service"), _candidate("refund_service")])
    )
    assert profile.mode == "llm"
    assert profile.direct_agent is None
    # 两个候选的线索都不完整（只有描述）→ 健康警告
    assert SceneWarningCode.AGENT_DESCRIPTION_ONLY.value in profile.warnings


def test_llm_mode_with_single_agent_still_uses_router():
    profile = build_scene_router_profile(
        _context([_candidate("only_one")], router_mode="llm")
    )
    assert profile.mode == "llm"
    assert profile.direct_agent is None


def test_direct_mode_with_multi_agents_picks_pool_first():
    profile = build_scene_router_profile(
        _context([_candidate("a"), _candidate("b")], router_mode="direct")
    )
    assert profile.mode == "direct"
    assert profile.direct_agent == "a"


def test_default_scene_always_direct():
    profile = build_scene_router_profile(
        _context([_candidate("default_assistant")], is_default=True, router_mode="direct")
    )
    assert profile.mode == "direct"
    assert profile.direct_agent == "default_assistant"


# ===== 兜底解析与降级 =====
def test_configured_fallback_in_pool_wins():
    target = _candidate("refund_service")
    profile = build_scene_router_profile(
        _context(
            [_candidate("order_service"), target],
            fallback_agent_id=target["id"],
        )
    )
    assert profile.fallback_agent == "refund_service"
    assert profile.fallback_source == FALLBACK_SOURCE_SCENE
    assert SceneWarningCode.FALLBACK_NOT_IN_POOL.value not in profile.warnings


def test_configured_fallback_missing_degrades_with_warning():
    profile = build_scene_router_profile(
        _context(
            [_candidate("order_service"), _candidate(DEFAULT_ASSISTANT_NAME)],
            fallback_agent_id=str(uuid.uuid4()),
        )
    )
    # 配置的兜底不在池内：降级到全局优先候选，并明确告警
    assert profile.fallback_agent == DEFAULT_ASSISTANT_NAME
    assert profile.fallback_source == FALLBACK_SOURCE_CONFIGURED_MISSING
    assert SceneWarningCode.FALLBACK_NOT_IN_POOL.value in profile.warnings


def test_fallback_prefers_default_assistant_when_no_scene_override():
    profile = build_scene_router_profile(
        _context([_candidate("order_service"), _candidate(DEFAULT_ASSISTANT_NAME)])
    )
    assert profile.fallback_agent == DEFAULT_ASSISTANT_NAME
    assert profile.fallback_source == FALLBACK_SOURCE_PREFERRED


# ===== 继承 / 覆盖 =====
def test_scene_overrides_global_defaults_with_source_labels():
    profile = build_scene_router_profile(
        _context(
            [_candidate("a"), _candidate("b")],
            router_model="scene-model",
            router_confidence_threshold=0.72,
            router_max_intents=3,
        )
    )
    assert (profile.model, profile.model_source) == ("scene-model", "scene")
    assert (profile.confidence_threshold, profile.threshold_source) == (0.72, "scene")
    assert (profile.max_intents, profile.max_intents_source) == (3, "scene")


def test_unset_overrides_inherit_global_defaults():
    profile = build_scene_router_profile(
        _context([_candidate("a"), _candidate("b")])
    )
    assert profile.model == settings.router_model
    assert profile.model_source == "global"
    assert profile.confidence_threshold == settings.router_confidence_threshold
    assert profile.threshold_source == "global"
    assert profile.max_intents == max(1, int(settings.router_max_intents))
    assert profile.max_intents_source == "global"


def test_fallback_text_override_and_default():
    custom = build_scene_router_profile(
        _context([_candidate("a")], fallback_text="  请稍等，我帮您转接。  ")
    )
    assert custom.fallback_text == "请稍等，我帮您转接。"
    assert custom.fallback_text_source == "scene"

    default = build_scene_router_profile(_context([_candidate("a")]))
    assert default.fallback_text == DEFAULT_FALLBACK_TEXT
    assert default.fallback_text_source == "default"


# ===== 提示词与指纹 =====
def test_prompt_contains_scene_scope_and_extra_rules():
    profile = build_scene_router_profile(
        _context(
            [_candidate("order_service", keywords=["订单"]), _candidate("refund_service")],
            extra_router_prompt="客户称呼为“亲”时优先转售后。",
        )
    )
    assert "code: mall_order" in profile.prompt
    assert "本场景可用 Agent：order_service、refund_service" in profile.prompt
    assert "场景补充规则：客户称呼为“亲”时优先转售后。" in profile.prompt
    assert "- order_service：" in profile.prompt
    assert "关键词：订单" in profile.prompt


def test_prompt_hash_stable_and_sensitive_to_config():
    candidates = [_candidate("order_service", description="订单查询"), _candidate("refund_service")]
    first = build_scene_router_profile(_context(candidates))
    second = build_scene_router_profile(_context(candidates))
    assert first.prompt_hash == second.prompt_hash
    assert len(first.prompt_hash) == 12

    changed_desc = [
        _candidate("order_service", description="订单查询与改地址"),
        _candidate("refund_service"),
    ]
    assert build_scene_router_profile(_context(changed_desc)).prompt_hash != first.prompt_hash

    # 场景补充规则会影响提示词，同样要反映到指纹上
    with_extra = build_scene_router_profile(
        _context(candidates, extra_router_prompt="额外规则")
    )
    assert with_extra.prompt_hash != first.prompt_hash


def test_profile_dict_exposes_candidates_and_warnings():
    profile = build_scene_router_profile(
        _context([_candidate("order_service", keywords=["订单"], examples=["订单到哪了"])])
    )
    data = profile_to_dict(profile)
    assert data["scene_code"] == "mall_order"
    assert data["candidate_count"] == 1
    assert data["candidates"][0]["name"] == "order_service"
    assert data["candidates"][0]["clue_level"] == "full"
    assert data["prompt_chars"] > 0
    assert isinstance(data["warnings"], list)
    # 详情不返回提示词正文，正文走 /prompt 接口
    assert "prompt" not in data


# ===== 警告 =====
def test_disabled_scene_warning():
    profile = build_scene_router_profile(
        _context([_candidate("a"), _candidate("b")], status="paused")
    )
    assert SceneWarningCode.SCENE_DISABLED.value in profile.warnings


# ===== RouterAgent 按档案路由 =====
class _FakeSimpleAgent:
    last_prompt = ""
    response = ""

    def __init__(self, *, name, llm, system_prompt, tool_registry=None):
        _FakeSimpleAgent.last_prompt = system_prompt

    def run(self, query):
        return _FakeSimpleAgent.response


class _Collector:
    def __init__(self):
        self.spans = []
        self.unique = []
        self.router_ms = None
        self.router_fallback = False
        self.prompt_hash = None

    def now_ms(self):
        return 5

    def add(self, *args, **kwargs):
        self.spans.append((args, kwargs))

    def add_unique(self, *args):
        self.unique.append(args)


@pytest.fixture
def fake_router_llm(monkeypatch):
    monkeypatch.setattr(ra, "get_llm", lambda model=None: object())
    monkeypatch.setattr(ra, "SimpleAgent", _FakeSimpleAgent)


@pytest.mark.asyncio
async def test_route_uses_profile_candidates_and_prompt(fake_router_llm):
    profile = build_scene_router_profile(
        _context(
            [_candidate("order_service", keywords=["订单"]), _candidate("refund_service", keywords=["退款"])]
        )
    )
    _FakeSimpleAgent.response = json.dumps(
        {"intents": [{"agent_name": "refund_service", "confidence": 0.91, "reason": "提到退款"}]}
    )
    result = await ra.RouterAgent().route("我要退款", profile=profile)

    # 提示词只包含本场景候选，且带场景约束段
    assert "- refund_service：" in _FakeSimpleAgent.last_prompt
    assert f"- {DEFAULT_ASSISTANT_NAME}：" not in _FakeSimpleAgent.last_prompt
    assert "code: mall_order" in _FakeSimpleAgent.last_prompt
    assert result["intents"][0]["agent_name"] == "refund_service"
    assert result["mode"] == "llm"
    assert result["prompt_hash"] == profile.prompt_hash


@pytest.mark.asyncio
async def test_route_rejects_agent_outside_scene_pool(fake_router_llm):
    profile = build_scene_router_profile(
        _context([_candidate("order_service"), _candidate(DEFAULT_ASSISTANT_NAME)])
    )
    _FakeSimpleAgent.response = json.dumps(
        {"intents": [{"agent_name": "refund_service", "confidence": 0.99, "reason": "臆造"}]}
    )
    result = await ra.RouterAgent().route("退款", profile=profile)

    # 池外名称被丢弃 → 回落到档案派生的兜底 Agent（default_assistant），而不是全局清单
    assert result["intents"][0]["agent_name"] == DEFAULT_ASSISTANT_NAME
    assert result["intents"][0]["is_fallback"] is True


@pytest.mark.asyncio
async def test_route_records_scene_span_fields(fake_router_llm):
    profile = build_scene_router_profile(
        _context([_candidate("order_service"), _candidate("refund_service")])
    )
    _FakeSimpleAgent.response = json.dumps(
        {"intents": [{"agent_name": "order_service", "confidence": 0.8, "reason": "订单"}]}
    )
    collector = _Collector()
    await ra.RouterAgent().route("查订单", profile=profile, collector=collector)

    span_kwargs = collector.spans[0][1]
    assert span_kwargs["scene_code"] == "mall_order"
    assert span_kwargs["candidate_count"] == 2
    assert span_kwargs["prompt_hash"] == profile.prompt_hash
    assert span_kwargs["router_mode"] == "llm"
    # 拍平字段供后台统计
    assert collector.prompt_hash == profile.prompt_hash
    assert collector.router_ms == 0
    assert collector.router_fallback is False
