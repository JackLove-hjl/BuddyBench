"""对话编排:落库 user → (必要时自动压缩) → agent 流式 → (必要时挂起等审批) → 落库 assistant。

agent 多轮上下文在 LangGraph checkpointer(thread_id = conversation_id);
checkpointer 无该线程历史时,从 DB 回放最近 N 条消息兜底。

参考 deepseek-harness 的三处机制:
1. **自动上下文压缩** —— 请求前按 token 压力预判(harness 的 `agent/pre-step`)
2. **溢出恢复** —— provider 报上下文超限时压缩后重试一次(harness 的 `agent/request-error`)
3. **执行期人工审批** —— 危险操作挂起等待批准/拒绝(harness 的 user-approval `ask`),
   批准后由 `/api/chat/approve` 用 `Command(resume=...)` 从中断点继续
"""
import base64
import logging
import mimetypes
import re
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import bridge, fragments
from app.core.config import get_settings
from app.core.registry import ModelRegistry
from app.db.models import Conversation, Message
from app.schemas.chat import ChatRequest
from app.services import compaction
from app.services.images import image_url_to_data_url

logger = logging.getLogger(__name__)

IMAGE_RE = re.compile(r"/images/[0-9a-fA-F-]{36}\.(?:png|jpe?g|webp)")


class _StreamState:
    """一次流式请求的可变累积状态(供桥接层写入、结束后统一落库)。"""

    def __init__(self, model: str, prior_active_ms: int = 0) -> None:
        self.model = model
        self.content_parts: list[str] = []
        self.usage: dict = {}
        self.tool_calls: list[str] = []
        self.tool_records: list[dict] = []
        self.reasoning_parts: list[str] = []
        self.timeline: list[dict] = []
        self.events: list[dict] = []
        self.error: str | None = None
        # 本轮"干活"用时:本次请求之前已累计的部分(审批续跑时从上一段挂起消息里取),
        # 加上本次请求从进来到现在的时间。等待用户审批的那段没有任何请求在跑,
        # 因此天然不计入 —— 这正是"用时"该有的口径。
        self.prior_active_ms = max(0, int(prior_active_ms))
        self.started_at = time.monotonic()

    def active_ms(self) -> int:
        """截至此刻本轮的干活用时。"""
        return self.prior_active_ms + int((time.monotonic() - self.started_at) * 1000)


async def _touch_conversation(session: AsyncSession, conversation_id: uuid.UUID) -> None:
    conv = await session.get(Conversation, conversation_id)
    if conv is not None:
        conv.updated_at = func.now()


async def _save_assistant(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    model_id: str,
    content_parts: list[str],
    usage: dict,
    tool_calls: list[str],
    error: str | None,
    tool_records: list[dict] | None = None,
    reasoning: str | None = None,
    segments: list[dict] | None = None,
    events: list[dict] | None = None,
    approval: dict | None = None,
    active_ms: int | None = None,
    duration_ms: int | None = None,
) -> Message:
    content = "".join(content_parts)
    meta: dict = {
        "images": list(dict.fromkeys(IMAGE_RE.findall(content))),  # 去重保序
        "tool_calls": tool_calls,
    }
    # 完整工具执行记录(含耗时/状态/参数/结果),供刷新后回放
    if tool_records:
        meta["tool_records"] = tool_records
    if reasoning:
        meta["reasoning"] = reasoning
    # 按真实执行顺序的 思考块/工具卡/压缩 时间线,刷新后保持交错渲染
    if segments:
        meta["segments"] = segments
    # 原始事件流(增量合并后),用于精确重建文本/思考/工具的交错顺序
    if events:
        meta["events"] = events
    # 待人工审批的危险操作(刷新后仍可继续批准/拒绝)
    if approval:
        meta["pending_approval"] = {"actions": approval.get("action_requests") or []}
    if usage:
        meta["usage"] = usage
    if error:
        meta["error"] = error
    # 中间挂起(等审批)的消息记 active_ms:续跑时以此续算本轮干活用时
    if active_ms is not None:
        meta["active_ms"] = active_ms
    # 本轮收尾的消息记 duration_ms:前端在消息底部显示"用时 X 秒",刷新后仍在
    if duration_ms is not None:
        meta["duration_ms"] = duration_ms
    msg = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=content,
        model=model_id,
        meta=meta,
    )
    session.add(msg)
    await _touch_conversation(session, conversation_id)
    await session.commit()
    await session.refresh(msg)
    return msg


async def _prior_active_ms(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    """续跑时取本轮已累计的干活用时(ms)。

    中断处的 assistant 消息会写上 meta.active_ms(那一条同时带着 pending_approval)。
    只认**最后一条用户消息之后**的那条:更早的属于上一轮,不能接着累加。
    """
    last_user_at = await session.scalar(
        select(Message.created_at)
        .where(Message.conversation_id == conversation_id, Message.role == "user")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    row = (
        await session.execute(
            select(Message.meta, Message.created_at)
            .where(Message.conversation_id == conversation_id, Message.role == "assistant")
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return 0
    meta, created_at = row
    if last_user_at is not None and created_at < last_user_at:
        return 0  # 最后一条 assistant 属于上一轮,本轮从零开始
    value = (meta or {}).get("active_ms")
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def _graph_config(conversation_id: uuid.UUID) -> dict:
    """LangGraph 运行配置:thread_id + 单轮步数上限。

    recursion_limit 必须显式给:LangGraph 出厂默认只有 25,而这张图每轮「模型 → 工具」
    要消耗多个 superstep(中间件节点各自计数),大约 6 次工具调用就会打满 ——
    模型一旦陷入重复读文件之类的循环,用户看到的就是一句英文的 Recursion limit 报错。
    """
    return {
        "configurable": {"thread_id": str(conversation_id)},
        "recursion_limit": get_settings().agent_recursion_limit,
    }


def _friendly_error(message: str) -> str:
    """把少数「常见但用户看不懂」的上游报错换成可执行的中文提示;其余原样返回。"""
    if "Recursion limit" in message:
        limit = get_settings().agent_recursion_limit
        return (
            f"本轮工具调用步数超出上限({limit} 步),已中止 —— 通常是模型陷入了重复调用"
            "(例如反复读同一个文件)。直接发一句「继续」我就能接着做;"
            "需要更大上限时调环境变量 AGENT_RECURSION_LIMIT。"
        )
    return message


def _build_text_prompt(req: ChatRequest) -> str:
    """组装文本 prompt:正文 + 文本文件内嵌内容(图片不走 markdown,由多模态传入)。"""
    parts: list[str] = [req.message]
    for att in req.attachments:
        if att.kind == "image":
            # 图片交给多模态 image_url(视觉模型),不拼进文本,避免重复/模型不可见
            continue
        name = att.filename or "file"
        content = (att.content or "").strip()
        if content:
            parts.append(f"\n--- 文件 `{name}` 内容 ---\n{content}\n--- 文件结束 ---")
        else:
            parts.append(f"\n[附件文件 `{name}`]({att.url})\n")
    return "\n".join(parts)


def _image_to_base64(url: str) -> str | None:
    """把指向本机的图片 URL 转成 base64 data URL;失败(文件缺失/过大)返回 None。

    覆盖两类本机图片:
    - `/images/<uuid>.jpg|.png` —— 绘图与 computer use 的屏幕截图(落在 image_dir);
    - `/uploads/images/<uid>/<name>` —— 用户上传的图片附件。
    """
    if url.startswith("data:"):
        return url  # 已是 base64
    # computer use 截图 / 绘图产物:/images/ 下平铺的 uuid 文件名
    converted = image_url_to_data_url(url, Path(get_settings().image_dir))
    if converted:
        return converted
    m = re.search(r"/uploads/images/([0-9a-fA-F-]+)/([^?#\s]+)", url)
    if not m:
        return None
    uid, name = m.group(1), m.group(2)
    uploads_root = Path(get_settings().image_dir).parent / "uploads"
    local = uploads_root / "images" / uid / name
    if not local.is_file() or local.stat().st_size > 5 * 1024 * 1024:
        return None
    mime = mimetypes.guess_type(name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(local.read_bytes()).decode('ascii')}"


def _collect_image_urls(req: ChatRequest) -> list[str]:
    """收集图片附件,统一转成视觉模型可识别的 image_url。

    本机上传的图片一律转 base64 data URL(外部模型无需也无法回拉 localhost);
    已是 http(s) 的远程 URL 原样保留。本机文件缺失时跳过该图片。
    """

    def to_multimodal(url: str) -> str | None:
        if url.startswith(("http://", "https://")):
            return url
        return _image_to_base64(url)

    return [u for u in (to_multimodal(a.url) for a in req.attachments if a.kind == "image") if u]


def _fix_legacy_image_urls(messages) -> bool:
    """迁移历史多模态消息:把 content 中指向本机 uploads 的 http(s) URL 图片
    替换为 base64 data URL。checkpointer 中旧会话的图片以 URL 形式持久化,
    外部模型无法访问 localhost,需在发送前转换。返回是否有修改。"""
    changed = False
    for msg in messages:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        new_blocks = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                url = (block.get("image_url") or {}).get("url", "")
                if url.startswith(("http://", "https://")):
                    converted = _image_to_base64(url)
                    if converted:
                        block = {**block, "image_url": {"url": converted}}
                        changed = True
            new_blocks.append(block)
        if new_blocks != content:
            msg.content = new_blocks
    return changed


async def _build_history(
    session: AsyncSession,
    agent,
    config: dict,
    conversation_id: uuid.UUID,
    exclude_id: uuid.UUID | None,
    summary: str | None,
    keep_messages: int,
) -> list:
    """组装本次请求的上下文:优先用 checkpointer,空则从 DB 回放;压缩过则用 摘要+最近几条。"""
    state = await agent.aget_state(config)
    messages = (state.values or {}).get("messages") or []
    if not messages:
        if summary:
            recent = (
                await compaction.load_recent_history(
                    session, conversation_id, exclude_id, keep_messages
                )
                if exclude_id is not None
                else []
            )
            # 摘要是**系统注入的片段**(带类型标记),这样历史里能认出"这不是用户说过的话"
            # —— 需要按类型过滤/替换注入内容时(见 agent/fragments.py)才有依据。
            return [
                SystemMessage(
                    content=fragments.render("context.summary", f"先前对话摘要:\n{summary}")
                ),
                *recent,
            ]
        if exclude_id is None:
            return []
        return await compaction.load_recent_history(session, conversation_id, exclude_id, 50)
    # 迁移旧会话历史中指向本机 uploads 的 URL 图片 → base64,
    # 否则外部模型无法访问 localhost 图片(messages[n].image 下载失败)
    if _fix_legacy_image_urls(messages):
        await agent.aupdate_state(config, {"messages": messages})
    return []


def _compact_event(ok: bool, summary: str | None, note: str, timeline: list[dict]) -> str:
    """压缩事件:写进时间线(供刷新后回放)并返回 SSE 帧。"""
    if ok and summary:
        timeline.append({"kind": "compact", "summary": summary})
    return bridge.sse(
        "compact",
        {"type": "compact", "ok": ok, "summary": summary or "", "message": note},
    )


async def _consume_stream(
    *,
    agent,
    config: dict,
    conversation_id: uuid.UUID,
    session: AsyncSession,
    st: _StreamState,
    prompt: str,
    history: list,
    image_urls: list[str],
    registry: ModelRegistry,
    conversation: Conversation,
    resume: dict | None = None,
) -> AsyncIterator[str]:
    """跑一次图流:溢出压缩重试 → 中断检测 → 落库 → done。

    中断时**不发 done**,只发 approval 后结束本次流,等待前端调用恢复接口。
    """
    settings = get_settings()
    attempt = 0
    while True:
        attempt += 1
        try:
            async for frame in bridge.translate(
                agent, config, prompt, history, st.content_parts, st.tool_records,
                st.reasoning_parts, st.timeline, image_urls, st.events, resume,
            ):
                yield frame
            break
        except Exception as e:  # noqa: BLE001
            raw = str(e)
            message = _friendly_error(raw)
            # 仅在「确认是上下文超限」「尚未产生任何输出」「不是恢复请求」时压缩重试,
            # 避免重复已推给前端的部分产出(超限判定一律用原始文本,不受本地化提示影响)
            can_retry = (
                attempt == 1
                and not st.content_parts
                and resume is None
                and compaction.is_context_overflow(raw)
            )
            if not can_retry:
                st.error = message
                yield bridge.sse(
                    "error", {"type": "error", "code": "upstream_error", "message": message}
                )
                await _save_assistant(
                    session, conversation_id, st.model, st.content_parts, st.usage,
                    st.tool_calls, message, st.tool_records, "".join(st.reasoning_parts),
                    st.timeline, st.events, duration_ms=st.active_ms(),
                )
                return
            ok, new_summary, note = await compaction.compact_conversation_context(
                agent=agent,
                registry=registry,
                model_id=st.model,
                conversation=conversation,
                session=session,
                keep_messages=settings.compact_keep_messages,
            )
            if not ok:
                st.error = f"{message}(自动压缩失败:{note})"
                yield bridge.sse(
                    "error", {"type": "error", "code": "upstream_error", "message": st.error}
                )
                await _save_assistant(
                    session, conversation_id, st.model, st.content_parts, st.usage,
                    st.tool_calls, st.error, st.tool_records, "".join(st.reasoning_parts),
                    st.timeline, st.events, duration_ms=st.active_ms(),
                )
                return
            yield _compact_event(True, new_summary, "上下文超限,已压缩历史后重试", st.timeline)
            history = await _build_history(
                session, agent, config, conversation_id, None, new_summary,
                settings.compact_keep_messages,
            )

    # 中断检测:危险工具等待人工审批(harness 的 ask 语义)
    interrupt_value = None
    try:
        interrupt_value = bridge.extract_interrupt(await agent.aget_state(config))
    except Exception as e:  # noqa: BLE001  取状态失败视为未中断
        st.error = None
        logger.warning("读取中断状态失败: %s", e)

    if interrupt_value:
        actions = interrupt_value.get("action_requests") or []
        yield bridge.sse("approval", {"type": "approval", "actions": actions})
        await _save_assistant(
            session, conversation_id, st.model, st.content_parts, st.usage, st.tool_calls,
            None, st.tool_records, "".join(st.reasoning_parts), st.timeline, st.events,
            # 挂起消息带上"已干活的用时":批准续跑后从这里接着累加
            approval=interrupt_value, active_ms=st.active_ms(),
        )
        return

    try:
        final = (await agent.aget_state(config)).values.get("messages") or []
        for m in final:
            if isinstance(m, AIMessage):
                st.tool_calls.extend(c.name for c in (m.tool_calls or []))
                if m.usage_metadata:
                    st.usage = {
                        "prompt_tokens": m.usage_metadata.get("input_tokens"),
                        "completion_tokens": m.usage_metadata.get("output_tokens"),
                    }
    except Exception:  # noqa: BLE001  取终态失败不影响主流程
        pass

    duration_ms = st.active_ms()
    # usage 兜底:部分网关(实测本机配置的 coding plan)流式不返回 usage_metadata,
    # 前端上下文圆圈就永远是 0,刷新后更无从恢复。用与压缩判定同一套近似计数器估一个,
    # 让圆圈至少反映真实量级(拿到真实 usage 时不会被覆盖)。
    if not st.usage.get("prompt_tokens"):
        try:
            state_messages = (await agent.aget_state(config)).values.get("messages") or []
            estimated = compaction.estimate_tokens(state_messages)
            if estimated:
                st.usage = {**st.usage, "prompt_tokens": estimated}
        except Exception as e:  # noqa: BLE001  估算失败不影响主流程
            logger.warning("估算上下文占用失败: %s", e)
    msg = await _save_assistant(
        session, conversation_id, st.model, st.content_parts, st.usage, st.tool_calls,
        st.error, st.tool_records, "".join(st.reasoning_parts), st.timeline, st.events,
        duration_ms=duration_ms,
    )
    yield bridge.sse(
        "done",
        {
            "type": "done",
            "message_id": str(msg.id),
            "model": st.model,
            "images": msg.meta.get("images", []),
            "usage": st.usage,
            # 本轮干活用时(前端显示在回复底部"用时 X 秒",刷新后从 meta 里读回来)
            "duration_ms": duration_ms,
            # 用模型自己声明的输入上下文(未配置则回退默认),前端圆圈的分母
            "context_window": registry.context_window(st.model, settings.context_window_default),
        },
    )


async def stream_chat(
    req: ChatRequest,
    agent,
    session: AsyncSession,
    conversation: Conversation,
    registry: ModelRegistry,
) -> AsyncIterator[str]:
    """SSE 生成器:compact/delta/reasoning/tool/approval → done / error。异常在生成器内消化。"""
    settings = get_settings()
    prompt = _build_text_prompt(req)
    image_urls = _collect_image_urls(req)
    # 落库的用户消息 content 只存用户原始输入(不内嵌附件/文件内容),
    # 避免刷新对话后文件内容出现在用户消息泡中;完整 prompt 只用于发给 agent
    user_msg = Message(
        conversation_id=req.conversation_id,
        role="user",
        content=req.message,
        meta={"attachments": [a.model_dump(exclude={"content"}) for a in req.attachments]}
        if req.attachments
        else {},
    )
    session.add(user_msg)
    await _touch_conversation(session, req.conversation_id)
    await session.commit()
    await session.refresh(user_msg)

    config = _graph_config(req.conversation_id)
    st = _StreamState(req.model)
    summary = conversation.summary

    try:
        # ① 请求前预判:上下文占用过高时先压缩(harness 的 pre-step 时机)
        if not summary:
            compacted, new_summary = await compaction.auto_compact_if_needed(
                agent=agent,
                registry=registry,
                model_id=req.model,
                conversation=conversation,
                session=session,
                system_prompt="",
                # 阈值基于模型真实窗口:1M 上下文的模型不该按 128k 默认值触发压缩
                context_window=registry.context_window(req.model, settings.context_window_default),
                trigger_fraction=settings.compact_trigger_fraction,
                keep_messages=settings.compact_keep_messages,
            )
            if compacted and new_summary:
                summary = new_summary
                yield _compact_event(True, new_summary, "上下文占用过高,已自动压缩历史", st.timeline)

        history = await _build_history(
            session, agent, config, req.conversation_id, user_msg.id, summary,
            settings.compact_keep_messages,
        )
        async for frame in _consume_stream(
            agent=agent,
            config=config,
            conversation_id=req.conversation_id,
            session=session,
            st=st,
            prompt=prompt,
            history=history,
            image_urls=image_urls,
            registry=registry,
            conversation=conversation,
        ):
            yield frame
    except Exception as e:  # noqa: BLE001  落库等内部错误
        yield bridge.sse("error", {"type": "error", "code": "internal", "message": str(e)})


# 计划获批后自动开一轮实施时用的指令(批准是界面动作,不是用户打出来的话)
PLAN_APPROVED_PROMPT = "用户已批准上面的计划,请按计划开始实施。"


async def start_implementation(
    *,
    conversation_id: uuid.UUID,
    model: str,
    agent,
    session: AsyncSession,
    conversation: Conversation,
    registry: ModelRegistry,
    instruction: str = PLAN_APPROVED_PROMPT,
) -> AsyncIterator[str]:
    """计划获批后直接开一轮实施(**用非计划模式的图**)。

    为什么不用 `Command(resume=...)` 恢复计划图:
    计划模式下写类工具从未装配(见 manager.plan_mode_hidden_tools),恢复后模型拿不到
    write_file / run_shell_command,提示词却说"计划模式已结束、请直接实施" ——
    于是它只能在"已批准"与"没有工具"之间打转,甚至再提交一次计划(实测如此)。

    改为:获批后立刻用非计划模式的图发一条「计划已批准,请开始实施」。
    计划图上那次挂起(exit_plan_mode)不再消费 —— 实测同一 thread 的后续轮次不受影响,
    悬挂的 exit_plan_mode 调用由 PatchToolCallsMiddleware 修补。
    """
    config = _graph_config(conversation_id)
    # 计划获批接着实施:本轮用时从计划那一段继续累加(对用户而言是同一轮)
    st = _StreamState(model, prior_active_ms=await _prior_active_ms(session, conversation_id))
    try:
        async for frame in _consume_stream(
            agent=agent,
            config=config,
            conversation_id=conversation_id,
            session=session,
            st=st,
            prompt=instruction,
            history=[],
            image_urls=[],
            registry=registry,
            conversation=conversation,
        ):
            yield frame
    except Exception as e:  # noqa: BLE001  落库等内部错误
        yield bridge.sse("error", {"type": "error", "code": "internal", "message": str(e)})


async def resume_chat(
    *,
    conversation_id: uuid.UUID,
    model: str,
    decisions: list[dict],
    agent,
    session: AsyncSession,
    conversation: Conversation,
    registry: ModelRegistry,
) -> AsyncIterator[str]:
    """人工审批后恢复执行:用 `Command(resume=...)` 从中断点继续流式输出。

    只发流式事件,不重复落库用户消息(中断处的状态已包含本轮输入)。
    """
    config = _graph_config(conversation_id)
    # 审批续跑:接着挂起前的用时累加(等待审批的那段不算)
    st = _StreamState(model, prior_active_ms=await _prior_active_ms(session, conversation_id))
    try:
        async for frame in _consume_stream(
            agent=agent,
            config=config,
            conversation_id=conversation_id,
            session=session,
            st=st,
            prompt="",
            history=[],
            image_urls=[],
            registry=registry,
            conversation=conversation,
            resume={"decisions": decisions},
        ):
            yield frame
    except Exception as e:  # noqa: BLE001
        yield bridge.sse("error", {"type": "error", "code": "internal", "message": str(e)})
