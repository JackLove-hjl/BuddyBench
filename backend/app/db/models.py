"""业务表 ORM:users / conversations / providers / messages。

A1 改造:新增 users 表;conversations 与 providers 通过 user_id 外键与用户绑定,
实现"大模型配置和对话跟用户绑定"。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")
    providers: Mapped[list["Provider"]] = relationship(back_populates="user")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    # Agent 权限(read_only / workspace_writable / full_access,默认 read_only)
    permission: Mapped[str] = mapped_column(String(30), default="read_only")
    # 工作区根目录(绝对路径);未设置时为空
    workspace_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # 计划模式:开启后模型只探索并提交计划(exit_plan_mode),经用户批准后才开始实施
    plan_mode: Mapped[bool] = mapped_column(default=False, server_default="false")
    # 置顶:列表排序时优先于 updated_at 倒序(用户手动置顶的会话排在最前)
    pinned: Mapped[bool] = mapped_column(default=False, server_default="false")
    # 上下文压缩摘要(/compact 生成;存在时下次对话用摘要 + 最近几条替代完整历史)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Provider(Base):
    """用户自建的 LLM 供应商配置(WebUI 管理,替代静态 .env LLM_PROVIDERS)。"""

    __tablename__ = "providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))  # 实例名称(前端展示/标识);唯一性改为 (user_id, name)
    provider_type: Mapped[str] = mapped_column(String(50))  # openai / anthropic / deepseek / qwen / custom...
    base_url: Mapped[str] = mapped_column(String(500))  # OpenAI 兼容端点(可修改)
    api_key: Mapped[str] = mapped_column(String(500), default="")
    # 最近一次同步的模型列表 [{"id": "...", "display_name": "..."}]
    models: Mapped[dict] = mapped_column(JSONB, default=list)
    # 手动补充的模型名(/models 未返回时,如 deepseek-v4-flash-vision-exp),同步时合并进 models
    extra_models: Mapped[dict] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="providers")

    __table_args__ = (Index("idx_providers_user_name", "user_id", "name", unique=True),)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 生成该条消息的模型
    # {"reasoning": str, "tool_calls": [{"name","args"}], "images": [url],
    #  "usage": {"prompt_tokens","completion_tokens"}, "error": str, "attachments": [...]}
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (Index("idx_messages_conv_created", "conversation_id", "created_at"),)
