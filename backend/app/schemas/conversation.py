"""会话/消息 API 模型。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Agent 权限常量
PERMISSION_READ_ONLY = "read_only"
PERMISSION_WORKSPACE_WRITABLE = "workspace_writable"
PERMISSION_FULL_ACCESS = "full_access"


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    # 新建会话时可选指定权限(默认 read_only)与工作区
    permission: str = Field(default="read_only", description="read_only / workspace_writable / full_access")
    workspace_path: str | None = Field(default=None, max_length=1000)
    plan_mode: bool = Field(default=False, description="计划模式:先出计划,经批准后再实施")


class ConversationUpdate(BaseModel):
    """更新会话配置(权限/工作区/计划模式/标题/置顶)。"""

    title: str | None = Field(default=None, max_length=200)
    permission: str | None = Field(default=None, description="read_only / workspace_writable / full_access")
    workspace_path: str | None = Field(default=None, max_length=1000)
    plan_mode: bool | None = Field(default=None, description="计划模式开关")
    pinned: bool | None = Field(default=None, description="置顶:列表排序优先")


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    permission: str = PERMISSION_READ_ONLY
    workspace_path: str | None = None
    plan_mode: bool = False
    pinned: bool = False
    summary: str | None = None
    created_at: datetime
    updated_at: datetime


class CompactRequest(BaseModel):
    """压缩上下文:model 为当前会话使用的模型(用于生成摘要)。"""

    model: str = Field(min_length=1, max_length=200)


class ConversationListItem(ConversationOut):
    message_count: int = 0


class ConversationList(BaseModel):
    items: list[ConversationListItem]
    total: int


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    model: str | None = None
    meta: dict = Field(default_factory=dict)
    created_at: datetime


class MessageList(BaseModel):
    items: list[MessageOut]
    total: int
