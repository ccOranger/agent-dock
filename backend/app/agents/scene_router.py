"""场景路由档案：由场景配置、Agent 池与全局默认派生出的只读视图。

路由不是一个实体：它不进 agents 表，也没有独立的“编辑路由”入口。
每个场景按库中现状实时派生一份档案，同一份档案同时服务：

- 运行时：``RouterAgent.route(profile)`` 只从本场景候选里选，阈值 / 意图上限 /
  兜底 Agent 全部取档案，不再二次查库或全局配置；
- 后台：路由页展示的生效参数、候选线索、健康警告与提示词与运行时时完全同源。

这样“不同场景路由信息不同”是天然结果，而不是需要同步维护的第二份数据。
"""
from __future__ import annotations

import hashlib

from app.agents.prompts.router import render_system_prompt
from app.agents.router_agent import FALLBACK_CANDIDATES, RouterAgent, _candidate_brief
from app.core.config import settings
from app.schemas.scene import (
    DEFAULT_FALLBACK_TEXT,
    RouterEffectiveMode,
    RouterMode,
    SceneContext,
    SceneRouterProfile,
    SceneStatus,
    SceneWarningCode,
)

# fallback_source 取值：说明兜底 Agent 是怎么来的
FALLBACK_SOURCE_SCENE = "scene"  # 场景配置的 fallback_agent_id 在池内
FALLBACK_SOURCE_CONFIGURED_MISSING = "configured_missing"  # 配置了但不在池内，已降级
FALLBACK_SOURCE_PREFERRED = "preferred"  # 全局优先候选在池内
FALLBACK_SOURCE_POOL_FIRST = "pool_first"  # 池内首项
FALLBACK_SOURCE_EMPTY = "empty"  # 池为空


def build_scene_router_profile(scene: SceneContext) -> SceneRouterProfile:
    """按场景快照派生路由档案（纯函数，不访问数据库与模型）。

    场景快照由 ``scene_service.load_scene_context`` 一次性装配：
    候选列表即 ``SceneRepo.list_bound_agents`` 的结果，与后台看到的是同一份。
    """
    # 候选补上线索完整度与截断信息：既是后台展示数据，也是提示词渲染输入
    candidates = tuple(_candidate_brief(agent) for agent in (scene.candidates or ()))
    names = [c["name"] for c in candidates if c.get("name")]

    mode = _effective_mode(scene, len(names))
    model, model_source = _resolve_inherit(scene.router_model, settings.router_model)
    threshold, threshold_source = _resolve_inherit(
        scene.router_confidence_threshold, settings.router_confidence_threshold
    )
    max_intents, max_intents_source = _resolve_inherit(
        scene.router_max_intents, settings.router_max_intents
    )
    max_intents = max(1, int(max_intents))

    fallback_agent, fallback_source = _resolve_fallback(scene, candidates, names)
    # 直连目标与兜底目标同源：默认场景/单 Agent 场景下，“本轮交给谁”就是池内兜底项
    direct_agent = fallback_agent if mode is RouterEffectiveMode.DIRECT else None
    fallback_text, fallback_text_source = _resolve_fallback_text(scene)
    scene_block = _render_scene_block(scene, names)
    prompt = render_system_prompt(
        agent_list=RouterAgent._format_agent_list(list(candidates)),
        scene_rules=scene_block,
        max_intents=max_intents,
        fallback_agent=fallback_agent or "（无可用 Agent）",
    )
    warnings = _collect_warnings(scene, candidates, names, mode, fallback_source)

    return SceneRouterProfile(
        scene_id=scene.scene_id,
        scene_code=scene.code,
        scene_name=scene.name or scene.code,
        scene_status=scene.status,
        is_default=scene.is_default,
        mode=mode.value,
        direct_agent=direct_agent,
        configured_mode=scene.router_mode,
        model=model,
        model_source=model_source,
        confidence_threshold=float(threshold),
        threshold_source=threshold_source,
        max_intents=max_intents,
        max_intents_source=max_intents_source,
        fallback_agent=fallback_agent,
        fallback_source=fallback_source,
        fallback_text=fallback_text,
        fallback_text_source=fallback_text_source,
        candidates=candidates,
        warnings=tuple(warnings),
        prompt=prompt,
        # 用“无对话上下文”的提示词做哈希：它是场景路由配置的身份，
        # 不随每轮对话变化，才能把同一配置下的链路聚合起来对比。
        prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12],
        scene_block=scene_block,
    )


def profile_to_dict(profile: SceneRouterProfile) -> dict:
    """完整档案（后台详情页用）：含候选明细、警告与提示词指纹，不含提示词正文。"""
    return {
        "scene_id": profile.scene_id,
        "scene_code": profile.scene_code,
        "scene_name": profile.scene_name,
        "scene_status": profile.scene_status,
        "is_default": profile.is_default,
        "mode": profile.mode,
        "direct_agent": profile.direct_agent,
        "configured_mode": profile.configured_mode,
        "model": profile.model,
        "model_source": profile.model_source,
        "confidence_threshold": profile.confidence_threshold,
        "threshold_source": profile.threshold_source,
        "max_intents": profile.max_intents,
        "max_intents_source": profile.max_intents_source,
        "fallback_agent": profile.fallback_agent,
        "fallback_source": profile.fallback_source,
        "fallback_text": profile.fallback_text,
        "fallback_text_source": profile.fallback_text_source,
        "candidate_count": len(profile.candidates),
        "candidates": [dict(c) for c in profile.candidates],
        "warnings": list(profile.warnings),
        "prompt_chars": len(profile.prompt),
        "prompt_hash": profile.prompt_hash,
    }


def profile_summary(profile: SceneRouterProfile) -> dict:
    """列表摘要（不返回提示词与候选明细，场景多时保持响应轻量）。"""
    data = profile_to_dict(profile)
    data.pop("candidates", None)
    return data


def _effective_mode(scene: SceneContext, candidate_count: int) -> RouterEffectiveMode:
    """派生本轮实际生效模式（与 ``scene_service._pick_direct_agent`` 判定一致）。"""
    if candidate_count <= 0:
        return RouterEffectiveMode.UNCONFIGURED
    # 默认场景固定直连：无 scene_code 的请求不能承担路由 LLM 的额外延迟
    if scene.is_default or scene.router_mode == RouterMode.DIRECT.value:
        return RouterEffectiveMode.DIRECT
    if scene.router_mode == RouterMode.LLM.value:
        return RouterEffectiveMode.LLM
    # auto：单 Agent 直连，多 Agent 才值得走路由
    return (
        RouterEffectiveMode.DIRECT
        if candidate_count == 1
        else RouterEffectiveMode.LLM
    )


def _resolve_inherit(value, default):
    """场景覆盖优先，未配置时继承全局默认，并标注来源供后台显示「继承/覆盖」。"""
    if value is None:
        return default, "global"
    return value, "scene"


def _resolve_fallback(
    scene: SceneContext, candidates: tuple[dict, ...], names: list[str]
) -> tuple[str | None, str]:
    """解析本轮兜底 Agent：配置值优先且必须在池内，否则派生并给出原因。"""
    if scene.fallback_agent_id:
        target = str(scene.fallback_agent_id)
        for candidate in candidates:
            if str(candidate.get("id") or "") == target:
                return candidate["name"], FALLBACK_SOURCE_SCENE
        derived, _ = _derive_fallback(names)
        return derived, FALLBACK_SOURCE_CONFIGURED_MISSING
    return _derive_fallback(names)


def _derive_fallback(names: list[str]) -> tuple[str | None, str]:
    for preferred in FALLBACK_CANDIDATES:
        if preferred in names:
            return preferred, FALLBACK_SOURCE_PREFERRED
    if names:
        return names[0], FALLBACK_SOURCE_POOL_FIRST
    return None, FALLBACK_SOURCE_EMPTY


def _resolve_fallback_text(scene: SceneContext) -> tuple[str, str]:
    text = (scene.fallback_text or "").strip()
    if text:
        return text, "scene"
    return DEFAULT_FALLBACK_TEXT, "default"


def _render_scene_block(scene: SceneContext, names: list[str]) -> str:
    """渲染提示词里的场景约束段：候选边界 + 场景补充规则。"""
    lines = [f"- 场景：{scene.name or scene.code}（code: {scene.code}）"]
    if names:
        lines.append(f"- 本场景可用 Agent：{'、'.join(names)}")
        lines.append("- 只能从上述 Agent 中选择，不得输出清单之外的名称")
    else:
        lines.append("- 本场景尚未配置可用 Agent")
    extra = (scene.extra_router_prompt or "").strip()
    if extra:
        lines.append(f"- 场景补充规则：{extra}")
    return "\n".join(lines)


def _collect_warnings(
    scene: SceneContext,
    candidates: tuple[dict, ...],
    names: list[str],
    mode: RouterEffectiveMode,
    fallback_source: str,
) -> list[str]:
    """健康警告：只报“会影响路由效果”的事实，具体文案由前端按 code 映射。"""
    warnings: list[str] = []
    if not names:
        warnings.append(SceneWarningCode.NO_AGENTS.value)
    if scene.status != SceneStatus.ACTIVE.value:
        warnings.append(SceneWarningCode.SCENE_DISABLED.value)
    if fallback_source == FALLBACK_SOURCE_CONFIGURED_MISSING:
        warnings.append(SceneWarningCode.FALLBACK_NOT_IN_POOL.value)
    if mode is RouterEffectiveMode.DIRECT and len(names) == 1:
        warnings.append(SceneWarningCode.SINGLE_AGENT_DIRECT.value)
    if any(c.get("clue_level") == "description_only" for c in candidates):
        warnings.append(SceneWarningCode.AGENT_DESCRIPTION_ONLY.value)
    return warnings
