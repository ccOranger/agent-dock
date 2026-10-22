"""场景域枚举与常量。

这里的取值会同时出现在数据库 CHECK/默认值、后端 schema 与前端 types 中，
业务代码不要再散落字符串字面量。
"""
from dataclasses import dataclass, field
from enum import StrEnum

# 默认场景：无 scene_code 的请求落到这里。
# ID 固定，便于初始化脚本、文档与排障脚本引用；不要用随机 UUID。
DEFAULT_SCENE_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_SCENE_CODE = "default"
DEFAULT_SCENE_NAME = "默认场景"
# 默认场景绑定的内核级代码 Agent（中立问答，不绑定客服业务）
DEFAULT_ASSISTANT_NAME = "default_assistant"
# 默认知识库命名空间（默认场景唯一检索范围）
DEFAULT_KNOWLEDGE_NAMESPACE = "cs_default"


class SceneStatus(StrEnum):
    """场景生命周期。归档场景保留数据，但不再接受新对话。"""

    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class RouterMode(StrEnum):
    """场景路由模式。

    auto: 0 个 Agent=未配置、1 个=直连、>=2 个=LLM 路由
    llm: 强制走路由 LLM（仍要求至少 1 个 Agent）
    direct: 强制直连（要求恰好 1 个 Agent）
    """

    AUTO = "auto"
    LLM = "llm"
    DIRECT = "direct"


class ActorType(StrEnum):
    """会话/记忆归属类型。"""

    USER = "user"
    VISITOR = "visitor"


class RouterEffectiveMode(StrEnum):
    """本轮实际生效的路由方式（由 router_mode + 候选数派生，不是存储值）。"""

    DIRECT = "direct"
    LLM = "llm"
    UNCONFIGURED = "unconfigured"


# 场景未配置兜底话术时的全局默认文案（客户可见）
DEFAULT_FALLBACK_TEXT = "这个问题我先帮您转接人工客服，请稍候。"


class SceneWarningCode(StrEnum):
    """后台路由档案的健康警告。前端按 code 映射文案，不解析中文。"""

    NO_AGENTS = "no_agents"
    SINGLE_AGENT_DIRECT = "single_agent_direct"
    FALLBACK_NOT_IN_POOL = "fallback_not_in_pool"
    AGENT_DESCRIPTION_ONLY = "agent_description_only"
    NO_KNOWLEDGE_BASE = "no_knowledge_base"
    NO_IDENTITY_SCRIPT = "no_identity_script"
    SCENE_DISABLED = "scene_disabled"


def actor_key(actor_type: ActorType, actor_id: str) -> str:
    """记忆/会话的隔离键：user:<uuid> 或 visitor:<uuid>。"""
    return f"{actor_type.value}:{actor_id}"


@dataclass(frozen=True)
class Actor:
    """会话/记忆的归属主体。visitor 是匿名的，id 即 visitors.id。"""

    type: ActorType
    id: str

    @property
    def key(self) -> str:
        return actor_key(self.type, self.id)

    @property
    def is_visitor(self) -> bool:
        return self.type is ActorType.VISITOR


@dataclass(frozen=True)
class SceneContext:
    """一轮对话用到的场景快照（脱离 ORM Session 后仍可传递）。

    direct_agent 非空表示本轮跳过路由 LLM，直接交给该 Agent；
    这是默认场景与「单 Agent 场景」的唯一执行路径。

    M3 起的路由字段（model/threshold/max_intents/fallback/extra/candidates）
    由 resolve_scene 一次性填满，路由档案在运行时与后台完全同源，不再二次查库。
    """

    scene_id: str
    code: str
    is_default: bool
    router_mode: str
    agent_names: tuple[str, ...]
    direct_agent: str | None
    # ---- 路由档案输入（未填时表示按全局默认处理）----
    name: str = ""
    status: str = "active"
    router_model: str | None = None
    router_confidence_threshold: float | None = None
    router_max_intents: int | None = None
    fallback_agent_id: str | None = None
    fallback_text: str | None = None
    extra_router_prompt: str | None = None
    candidates: tuple[dict, ...] = ()
    # ---- 公开接入（鉴权与嵌入）----
    # 已发布场景才有 publish_key；无 Authorization 时据此判定可否匿名访问
    publish_key: str | None = None
    allowed_origins: tuple[str, ...] = ()
    widget_config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SceneRouterProfile:
    """场景路由档案：由场景配置 + Agent 池 + 全局默认派生出的只读视图。

    路由不是实体：它不进 agents 表，每次按库中现状重建；
    同一次构建结果同时服务运行时路由与后台查看，避免“看到的和跑的不一样”。
    """

    scene_id: str
    scene_code: str
    scene_name: str
    scene_status: str
    is_default: bool
    # 生效方式：direct / llm / unconfigured
    mode: str
    # 直连目标（仅 mode=direct 时非空）：运行时据此跳过路由 LLM
    direct_agent: str | None
    # 存储的模式：auto / llm / direct
    configured_mode: str
    model: str
    model_source: str
    confidence_threshold: float
    threshold_source: str
    max_intents: int
    max_intents_source: str
    fallback_agent: str | None
    fallback_source: str
    fallback_text: str
    fallback_text_source: str
    # 候选：与路由提示词里注入的 Agent 信息块一致
    candidates: tuple[dict, ...]
    warnings: tuple[str, ...]
    # 无对话上下文的提示词（后台预览与 prompt_hash 的基准）
    prompt: str
    prompt_hash: str
    # 渲染好的场景段落；运行时重新拼入本轮对话上下文
    scene_block: str

