"""回复反馈:👍 / 👎(👎 可带问题分类与详情)。

同一用户对同一条消息只保留一条记录 —— 再评价一次就覆盖上一次,
避免用户连点或先点 👍 再点 👎 时留下两条互相矛盾的记录。
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.base import get_session
from app.db.models import Feedback, User
from app.schemas.feedback import FeedbackRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("")
async def submit_feedback(
    body: FeedbackRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """提交/更新一条反馈。返回落库后的 id 与最终评价。"""
    row: Feedback | None = None
    if body.message_id:
        row = await session.scalar(
            select(Feedback).where(
                Feedback.user_id == user.id,
                Feedback.message_id == body.message_id,
            )
        )
    if row is None:
        row = Feedback(
            user_id=user.id,
            conversation_id=body.conversation_id,
            message_id=body.message_id,
            rating=body.rating,
        )
        session.add(row)
    row.rating = body.rating
    row.categories = body.categories
    row.detail = body.detail
    row.conversation_id = body.conversation_id or row.conversation_id
    meta = dict(row.meta or {})
    if body.model:
        meta["model"] = body.model
    row.meta = meta
    await session.commit()
    await session.refresh(row)
    logger.info("用户 %s 反馈 %s(message_id=%s)", user.username, row.rating, row.message_id)
    return {"ok": True, "id": str(row.id), "rating": row.rating}
