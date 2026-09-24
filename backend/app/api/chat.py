"""POST /api/chat:SSE 流式对话。

前端注意:本端点用 fetch POST + ReadableStream 解析(EventSource 不支持 POST)。
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import bridge
from app.agent.manager import AgentManager
from app.api.conversations import _get_conversation_or_404
from app.api.deps import get_agent_manager, get_current_user, get_registry
from app.core.registry import ModelError
from app.db.base import get_session
from app.db.models import Message, User
from app.schemas.chat import ApprovalRequest, ChatRequest
from app.services import chat_service
from app.tools.plan import PLAN_TOOL_NAME

router = APIRouter(tags=["chat"])


async def _clear_pending_approvals(session: AsyncSession, conversation_id: uuid.UUID) -> None:
    """用户已作出选择:清掉该会话消息上残留的待审批标记。

    挂起态落在 `meta.pending_approval` 里,前端按它渲染 问题卡 / 审批卡。它只在
    「正等用户决定」这个窗口期有效 —— 一旦用户答复/批准,本轮就会续跑并落新消息,
    而旧消息上的标记若不清掉,刷新页面后这些早已处理完的卡片会再次冒出来
    (点它们还会拿一份过期动作集去恢复一个早已恢复过的中断)。
    """
    rows = await session.scalars(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.meta.has_key("pending_approval"),
        )
    )
    changed = False
    for msg in rows.all():
        meta = dict(msg.meta or {})
        meta.pop("pending_approval", None)
        msg.meta = meta
        changed = True
    if changed:
        await session.commit()


async def _is_plan_approval(
    agent, conversation_id: uuid.UUID, decisions: list[dict]
) -> bool:
    """本次恢复是否为「批准计划」——即批准的是计划评审工具 exit_plan_mode。

    按工具名判断而不是"计划模式下的审批一律算批准":计划模式下(工作区可写档位)
    同样可能因写文件等危险操作挂起,那种批准不应该结束计划模式。
    """
    if not any(d.get("type") == "approve" for d in decisions):
        return False
    try:
        state = await agent.aget_state(
            {"configurable": {"thread_id": str(conversation_id)}}
        )
    except Exception:  # noqa: BLE001  读不到状态时按"非计划评审"处理
        return False
    interrupt = bridge.extract_interrupt(state)
    if not interrupt:
        return False
    return any(
        action.get("name") == PLAN_TOOL_NAME
        for action in interrupt.get("action_requests") or []
    )


@router.post("/chat")
async def chat(
    req: ChatRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    registry=Depends(get_registry),
    agent_manager: AgentManager = Depends(get_agent_manager),
):
    # 前置校验:失败直接返回 4xx JSON(而非 SSE);校验通过才进入流
    conv = await _get_conversation_or_404(session, req.conversation_id, user.id)
    try:
        # 按会话的权限/工作区/计划模式构建 agent(权限在工具层强制;联网工具常驻)
        agent = agent_manager.get_agent(
            req.model, conv.permission, conv.workspace_path, conv.plan_mode
        )
    except ModelError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})
    return StreamingResponse(
        chat_service.stream_chat(req, agent, session, conv, registry),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/approve")
async def approve(
    req: ApprovalRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    registry=Depends(get_registry),
    agent_manager: AgentManager = Depends(get_agent_manager),
) -> StreamingResponse:
    """人工审批后恢复执行(SSE)。

    危险操作被 HumanInTheLoopMiddleware 挂起后,前端弹出审批卡;用户批准/拒绝
    即调用本端点,决策经 `Command(resume={"decisions": [...]})` 注入中断点继续执行。

    计划评审**不走**这条恢复链路:批准 `exit_plan_mode` 时把 `plan_mode` 落库关闭,
    并**换成非计划模式的图直接开一轮实施** —— 计划图里写类工具从未装配,resume 只会让模型
    在"已批准"与"没有工具"之间打转(实测:它会试图写文件、被拒、然后再次提交计划)。
    详见 chat_service.start_implementation。

    注意:危险操作审批恢复**必须沿用产生本次中断的那个图**。HumanInTheLoopMiddleware 是在
    恢复时重放 `after_model`:它要重新用 `interrupt_on` 取审批配置,并在批准后把工具调用
    交回 ToolNode 执行 —— 换成"非计划模式"的新图会既匹配不到配置、又找不到相关工具。
    """
    conv = await _get_conversation_or_404(session, req.conversation_id, user.id)
    # 用户此刻已作出选择:挂起标记即刻作废(含刷新后重进会话的场景)
    await _clear_pending_approvals(session, req.conversation_id)
    decisions = [d.model_dump(exclude_none=True) for d in req.decisions] or [{"type": "reject"}]
    try:
        agent = agent_manager.get_agent(
            req.model, conv.permission, conv.workspace_path, conv.plan_mode
        )
        if conv.plan_mode and await _is_plan_approval(agent, req.conversation_id, decisions):
            # 计划获批:关闭计划模式,并换成非计划模式的图 —— 它才有写类工具,
            # 于是"点批准"这一个动作之后立刻进入实施(计划图 resume 做不到这一点)
            conv.plan_mode = False
            conv.updated_at = func.now()
            await session.commit()
            agent = agent_manager.get_agent(
                req.model, conv.permission, conv.workspace_path, False
            )
            return StreamingResponse(
                chat_service.start_implementation(
                    conversation_id=req.conversation_id,
                    model=req.model,
                    agent=agent,
                    session=session,
                    conversation=conv,
                    registry=registry,
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
    except ModelError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})
    return StreamingResponse(
        chat_service.resume_chat(
            conversation_id=req.conversation_id,
            model=req.model,
            decisions=decisions,
            agent=agent,
            session=session,
            conversation=conv,
            registry=registry,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
