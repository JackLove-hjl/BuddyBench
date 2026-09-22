"""会话 CRUD:创建/列表/消息/删除(按用户隔离)。"""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_agent_manager, get_current_user, get_registry
from app.agent.manager import AgentManager
from app.core.config import get_settings
from app.core.registry import ModelError, ModelRegistry
from app.db.base import get_session
from app.db.models import Conversation, Message, User
from app.schemas.conversation import (
    CompactRequest,
    ConversationCreate,
    ConversationList,
    ConversationListItem,
    ConversationOut,
    ConversationUpdate,
    MessageList,
    MessageOut,
)
from app.services import compaction

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _validate_workspace_path(path: str | None) -> str | None:
    """校验工作区目录存在且为目录,返回规范化后的绝对路径。"""
    if path is None or not path.strip():
        return None
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise HTTPException(status_code=422, detail={"code": "invalid_workspace", "message": "工作区必须是绝对路径"})
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=422, detail={"code": "invalid_workspace", "message": f"目录不存在: {p}"})
    return str(p.resolve())


async def _get_conversation_or_404(session: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID) -> Conversation:
    conv = await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation_not_found")
    return conv


@router.post("", status_code=201, response_model=ConversationOut)
async def create_conversation(
    body: ConversationCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Conversation:
    conv = Conversation(
        user_id=user.id,
        title=body.title or "新对话",
        permission=body.permission,
        workspace_path=_validate_workspace_path(body.workspace_path),
        plan_mode=body.plan_mode,
    )
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return conv


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Conversation:
    conv = await _get_conversation_or_404(session, conversation_id, user.id)
    if body.title is not None:
        conv.title = body.title
    if body.permission is not None:
        conv.permission = body.permission
    if body.workspace_path is not None:
        conv.workspace_path = _validate_workspace_path(body.workspace_path)
    if body.plan_mode is not None:
        conv.plan_mode = body.plan_mode
    if body.pinned is not None:
        conv.pinned = body.pinned
    conv.updated_at = func.now()
    await session.commit()
    await session.refresh(conv)
    return conv


@router.get("", response_model=ConversationList)
async def list_conversations(
    skip: int = 0,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ConversationList:
    total = (
        await session.scalar(
            select(func.count()).select_from(Conversation).where(Conversation.user_id == user.id)
        )
        or 0
    )
    rows = await session.execute(
        select(Conversation, func.count(Message.id).label("message_count"))
        .outerjoin(Message)
        .where(Conversation.user_id == user.id)
        .group_by(Conversation.id)
        # 置顶优先,其余按最近更新倒序
        .order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    items = [
        ConversationListItem(
            id=conv.id,
            title=conv.title,
            permission=conv.permission,
            workspace_path=conv.workspace_path,
            plan_mode=conv.plan_mode,
            pinned=conv.pinned,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            message_count=count,
        )
        for conv, count in rows.all()
    ]
    return ConversationList(items=items, total=total)


@router.get("/{conversation_id}/messages", response_model=MessageList)
async def get_messages(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> MessageList:
    await _get_conversation_or_404(session, conversation_id, user.id)
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    items = [MessageOut.model_validate(m) for m in rows.all()]
    return MessageList(items=items, total=len(items))


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    conv = await _get_conversation_or_404(session, conversation_id, user.id)
    await session.delete(conv)  # 级联删消息(relationship cascade)
    await session.commit()
    # 同步清 agent 记忆(thread_id = conversation_id)
    saver = get_checkpointer()
    if saver is not None:
        await saver.adelete_thread(str(conversation_id))


@router.post("/{conversation_id}/compact")
async def compact_conversation(
    conversation_id: uuid.UUID,
    body: CompactRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    agent_manager: AgentManager = Depends(get_agent_manager),
    registry: ModelRegistry = Depends(get_registry),
) -> dict:
    """压缩上下文:生成摘要 → 清空 checkpointer → 摘要持久化到会话。
    之后对话用 摘要 + 最近几条 替代完整历史,释放上下文窗口。

    与自动压缩共用 app/services/compaction.py 的同一条路径,保证行为一致。
    """
    conv = await _get_conversation_or_404(session, conversation_id, user.id)
    try:
        agent = agent_manager.get_agent(
            body.model, conv.permission, conv.workspace_path, conv.plan_mode
        )
    except ModelError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    ok, summary, note = await compaction.compact_conversation_context(
        agent=agent,
        registry=registry,
        model_id=body.model,
        conversation=conv,
        session=session,
        keep_messages=get_settings().compact_keep_messages,
    )
    if not ok:
        return {"compacted": False, "summary": None, "message": note}
    return {"compacted": True, "summary": summary, "message": note}
