"""接入方（服务端 API 调用方）请求模型。"""
from typing import Literal

from pydantic import BaseModel, Field

IntegrationStatusLiteral = Literal["active", "disabled"]


class IntegrationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, description="接入方名称，全局唯一")
    description: str | None = Field(default=None, max_length=500)
    # 授权的场景；不传表示暂不授权任何场景，Key 暂时无法调用任何聊天场景
    scene_ids: list[str] = Field(default_factory=list, max_length=50)


class IntegrationUpdate(BaseModel):
    """局部更新：未传字段保持原值；scene_ids 传了才全量替换。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    status: IntegrationStatusLiteral | None = None
    scene_ids: list[str] | None = Field(default=None, max_length=50)
