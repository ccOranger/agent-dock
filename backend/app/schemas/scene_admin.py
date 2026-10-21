"""场景管理请求模型（后台）。"""
from typing import Any, Literal

from pydantic import BaseModel, Field

SCENE_CODE_PATTERN = r"^[a-z][a-z0-9_]{1,31}$"

RouterModeLiteral = Literal["auto", "llm", "direct"]
SceneStatusLiteral = Literal["active", "paused", "archived"]


class SceneCreate(BaseModel):
    code: str = Field(
        min_length=2,
        max_length=32,
        pattern=SCENE_CODE_PATTERN,
        description="对外唯一标识，小写字母开头，仅含小写字母/数字/下划线",
    )
    display_name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    router_mode: RouterModeLiteral = "auto"
    fallback_agent_id: str | None = Field(default=None, max_length=36)
    fallback_text: str | None = Field(default=None, max_length=1000)
    extra_router_prompt: str | None = Field(default=None, max_length=4000)
    # 创建时可直接绑定 Agent，省一次请求
    agent_ids: list[str] = Field(default_factory=list, max_length=50)


class SceneUpdate(BaseModel):
    """局部更新：未传的字段保持原值；agent_ids 传了才全量替换。"""

    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    status: SceneStatusLiteral | None = None
    router_mode: RouterModeLiteral | None = None
    router_model: str | None = Field(default=None, max_length=64)
    router_confidence_threshold: float | None = Field(default=None, ge=0, le=1)
    router_max_intents: int | None = Field(default=None, ge=1, le=5)
    fallback_agent_id: str | None = Field(default=None, max_length=36)
    fallback_text: str | None = Field(default=None, max_length=1000)
    extra_router_prompt: str | None = Field(default=None, max_length=4000)
    agent_ids: list[str] | None = Field(default=None, max_length=50)


class SceneAgentsUpdate(BaseModel):
    """全量替换绑定；顺序即路由候选顺序。"""

    agent_ids: list[str] = Field(default_factory=list, max_length=50)


class SceneKnowledgeBasesUpdate(BaseModel):
    """全量替换知识库归属：列表内归本场景，列表外原属本场景的解绑为平台共享。"""

    namespaces: list[str] = Field(default_factory=list, max_length=100)


class SceneWidgetUpdate(BaseModel):
    """嵌入与公开接入配置；未传的字段保持原值。

    allow_anonymous=true 时若尚未发布则生成 publish_key；false 时清空。
    widget_config 是「全量替换」而非深合并：后台弹窗总是提交完整表单。
    """

    widget_config: dict[str, Any] | None = None
    allowed_origins: list[str] | None = Field(default=None, max_length=50)
    allow_anonymous: bool | None = None
