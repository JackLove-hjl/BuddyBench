"""回复反馈的请求体。"""
import uuid
from typing import Literal

from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    """一条反馈:rating 必填;👎 时可带问题分类与详情。"""

    rating: Literal["good", "bad"]
    conversation_id: uuid.UUID | None = None
    # 消息 id 可能是库里的 UUID,也可能是前端本地的 `local-err-…`,统一按字符串收
    message_id: str | None = Field(default=None, max_length=64)
    categories: list[str] = Field(default_factory=list, max_length=10)
    detail: str = Field(default="", max_length=5000)
    model: str | None = Field(default=None, max_length=100)
