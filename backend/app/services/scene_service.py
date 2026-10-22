"""场景解析：把对外的 scene_code 收敛成一轮对话可用的快照。"""
import hmac
import logging

from app.core.exceptions import (
    Forbidden,
    SceneDisabled,
    SceneNotConfigured,
    SceneNotFound,
    Unauthorized,
)
from app.models.repositories import AgentRepo, SceneRepo
from app.schemas.scene import (
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_SCENE_CODE,
    DEFAULT_SCENE_NAME,
    RouterMode,
    SceneContext,
    SceneStatus,
)

logger = logging.getLogger(__name__)


async def resolve_scene(scene_code: str | None) -> SceneContext:
    """解析请求场景（对话入口）。

    - 无 code → 默认场景（不回落其它场景，避免错配）；
    - code 不存在 → 3003；已停用/归档 → 3004；
    - 没有可用 Agent → 3005（不是 404：场景存在，只是还没配好）。
    """
    context = await load_scene_context(scene_code)
    if not context.agent_names:
        raise SceneNotConfigured()
    return context


async def load_scene_context(
    scene_code: str | None, *, require_active: bool = True
) -> SceneContext:
    """读库装配场景快照。

    后台路由页需要查看停用场景的档案（解释“为什么没生效”），
    因此这里把“状态校验”与“装配”分开，由调用方决定是否要求 active。
    """
    code = (scene_code or "").strip() or DEFAULT_SCENE_CODE
    scene = await SceneRepo.get_by_code(code)
    if scene is None:
        raise SceneNotFound()
    if require_active and scene.status != SceneStatus.ACTIVE.value:
        raise SceneDisabled()
    agents = await SceneRepo.list_bound_agents(scene.id)
    return build_scene_context(scene, agents)


def build_scene_context(scene, agents: list[dict]) -> SceneContext:
    """把 ORM 场景 + 绑定 Agent 装配成脱离 Session 的不可变快照。

    M3 起一次性填满路由档案输入（模型/阈值/意图上限/兜底/补充规则/候选），
    运行时路由与后台查看都基于这份快照，不再二次查库或读全局默认。
    """
    return SceneContext(
        scene_id=str(scene.id),
        code=scene.code,
        is_default=bool(scene.is_default),
        router_mode=scene.router_mode,
        agent_names=tuple(a["name"] for a in agents),
        direct_agent=_pick_direct_agent(scene, agents),
        name=scene.display_name,
        status=scene.status,
        router_model=scene.router_model,
        router_confidence_threshold=scene.router_confidence_threshold,
        router_max_intents=scene.router_max_intents,
        fallback_agent_id=str(scene.fallback_agent_id) if scene.fallback_agent_id else None,
        fallback_text=scene.fallback_text,
        extra_router_prompt=scene.extra_router_prompt,
        candidates=tuple(agents),
        # 旧测试/手工构造的场景快照未必带这些公开接入字段
        publish_key=getattr(scene, "publish_key", None),
        allowed_origins=tuple(getattr(scene, "allowed_origins", None) or ()),
        widget_config=dict(getattr(scene, "widget_config", None) or {}),
    )


def _pick_direct_agent(scene, agents: list[dict]) -> str | None:
    """派生本轮是否直连，以及直连哪个 Agent。

    默认场景固定 direct；auto 只在恰好 1 个可用 Agent 时直连，
    这样后台删光只剩一个 Agent 的场景仍然零配置可用。
    """
    if scene.router_mode == RouterMode.LLM.value:
        return None
    if scene.router_mode == RouterMode.DIRECT.value or scene.is_default:
        if scene.fallback_agent_id:
            target = str(scene.fallback_agent_id)
            for agent in agents:
                if agent["id"] == target:
                    return agent["name"]
        return agents[0]["name"]
    return agents[0]["name"] if len(agents) == 1 else None


def authorize_scene_access(
    scene: SceneContext,
    *,
    user,
    integration: dict | None = None,
    publish_key: str | None = None,
) -> None:
    """聊天入口鉴权：登录客户 / 接入方 API Key / 已发布场景匿名，三者取一。

    - 带了 Authorization 但 token 无效的情况已在依赖层报错，不会走到这里；
    - integration 是 X-API-Key 或 X-Integration-Code 反查出的接入方（含已授权场景 id），
      未命中在入口处已 401；匿名嵌入靠公开标识归属，但仍必须通过场景授权；
    - 场景未发布且未配置接入方时，匿名请求一律 401；
    - publish_key 是嵌入页里的公开标识，主动传错时按无效处理，便于排查配置。
    """
    if user is not None:
        if getattr(user, "role", "customer") != "customer":
            raise Forbidden("仅客户身份可发起对话")
        return

    if integration is not None:
        # 公开标识会出现在宿主页源码里，不能绕过场景的“开放匿名”开关
        if integration.get("via") == "public_code" and not scene.publish_key:
            raise Unauthorized("该场景未开放匿名访问")
        # 场景是业务维度，接入方是调用方维度：Key 有效不代表有权访问该场景
        if str(scene.scene_id) not in set(integration.get("scene_ids") or ()):
            raise Forbidden("该接入方未授权访问此场景")
        return

    if publish_key:
        # 先 encode：compare_digest 对非 ASCII str 会抛 TypeError（请求头可被伪造）
        if scene.publish_key and hmac.compare_digest(
            publish_key.encode("utf-8"), scene.publish_key.encode("utf-8")
        ):
            return
        raise Unauthorized("发布标识无效")

    if scene.publish_key:
        # 嵌入场景已发布：publish_key 本身是公开标识，不带也允许匿名（靠限流兜底）
        return

    raise Unauthorized("该场景未开放匿名访问，请先登录")


async def ensure_default_scene() -> None:
    """启动兜底：初始化脚本已种默认场景，这里只修复被误删/误改的情况。"""
    scene = await SceneRepo.get_default()
    if scene is not None:
        return

    existing = await SceneRepo.get_by_code(DEFAULT_SCENE_CODE)
    if existing is not None:
        await SceneRepo.update(existing.id, is_default=True, status=SceneStatus.ACTIVE.value)
        logger.warning("默认场景缺少 is_default/active 标记，已自动修复")
        return

    scene = await SceneRepo.create(
        code=DEFAULT_SCENE_CODE,
        display_name=DEFAULT_SCENE_NAME,
        description="无 scene_code 时的默认落点；单 Agent 直连，只读",
        is_default=True,
        router_mode=RouterMode.DIRECT.value,
    )
    assistant = await AgentRepo.get_by_name(DEFAULT_ASSISTANT_NAME)
    if assistant is not None:
        await SceneRepo.set_agents(scene.id, [str(assistant.id)])
    logger.warning("默认场景缺失，已按常量重建；建议执行 scripts/rebuild_and_seed.py 复核")
