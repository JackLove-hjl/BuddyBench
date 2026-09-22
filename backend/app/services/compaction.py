"""上下文压缩(自动 + 手动共用):token 压力预判、摘要生成、溢出恢复判定。

参考 deepseek-harness 的 compaction 插件,两个触发时机:
- **请求前按 token 压力触发**(harness 的 `agent/pre-step`)
- **provider 报上下文超限时压缩后重试**(harness 的 `agent/request-error`)

本项目的落地方式与手动 `/compact` 完全同一条路径:摘要写入 `Conversation.summary`,
并清空该会话的 checkpointer 线程;下一次请求以「摘要 + 最近 N 条」重建上下文。
这样自动压缩与手动压缩行为一致,不会出现两套压缩逻辑互相打架。
"""
import logging

from langchain_core.messages import SystemMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.registry import ModelRegistry
from app.db.checkpointer import get_checkpointer
from app.db.models import Conversation, Message

logger = logging.getLogger(__name__)

# 触发压缩的上下文占用比例(相对 context_window)
DEFAULT_TRIGGER_FRACTION = 0.7
# 压缩后保留的最近消息条数(摘要之外)
DEFAULT_KEEP_MESSAGES = 4
# 摘要输入最多取多少条消息,避免摘要请求自身超限
SUMMARY_INPUT_LIMIT = 80

# 判定 provider「上下文超限」错误的关键词(各平台措辞不一)
_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "context length",
    "maximum context",
    "max context",
    "context window",
    "too many tokens",
    "reduce the length",
    "prompt is too long",
    "input is too long",
    "string too long",
    "超出上下文",
    "上下文长度",
)


def messages_to_text(messages) -> str:
    """把消息序列化为文本(多模态取 text 部分),用于生成摘要。"""
    parts: list[str] = []
    for m in messages:
        role = getattr(m, "type", "message")
        content = getattr(m, "content", "")
        if isinstance(content, list):
            content = " ".join(
                str(b.get("text", ""))
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        text = str(content).strip()
        if not text or text in ("[发送] 压缩上下文",):
            continue
        parts.append(f"<{role}> {text}")
    return "\n".join(parts[-SUMMARY_INPUT_LIMIT:])


async def summarize_messages(registry: ModelRegistry, model_id: str, messages) -> str:
    """调用当前模型把对话历史压缩为中文摘要。"""
    history_text = messages_to_text(messages)
    if not history_text:
        return ""
    llm = registry.get_chat_model(model_id)
    prompt = (
        "你是对话摘要助手。请把以下 AI 助手与用户的对话历史压缩为一段简洁的中文摘要,"
        "保留:1)用户的核心诉求与已确认的目标;2)已经完成的决策/操作及结果;"
        "3)当前进行中的任务状态和下一步计划;4)重要约定(如语言、代码风格、文件位置)。"
        "控制在 400 字以内,只输出摘要正文。\n\n对话历史:\n"
        + history_text
    )
    resp = await llm.ainvoke(prompt)
    content = resp.content if hasattr(resp, "content") else str(resp)
    if isinstance(content, list):
        content = "".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return str(content).strip()[:2000]


def estimate_tokens(messages, system_prompt: str | None = None) -> int:
    """粗略估算上下文 token 数(消息 + 系统提示),仅用于触发阈值判断。

    用 langchain 的近似计数器(按字符数折算),避免为估算引入 tiktoken 依赖,
    也避免真实分词带来的开销。宁可略高估,以便更早触发压缩。
    """
    from langchain_core.messages.utils import count_tokens_approximately

    total = count_tokens_approximately(messages) if messages else 0
    if system_prompt:
        total += count_tokens_approximately([SystemMessage(content=system_prompt)])
    return int(total)


def is_context_overflow(error: str) -> bool:
    """判断错误文本是否属于「上下文超限」。

    各平台措辞不一(OpenAI `context_length_exceeded`、Anthropic `prompt is too long`、
    代理网关常直接透传 400 invalid_request_error),因此用关键词匹配,
    **只在明确的超限措辞上返回 True**,避免把别的 400 误当成超限去重试。
    """
    low = (error or "").lower()
    return any(marker in low for marker in _OVERFLOW_MARKERS)


async def load_recent_history(
    session: AsyncSession, conversation_id, exclude_id, limit: int
) -> list:
    """从 DB 回放最近 limit 条 user/assistant 文本消息(升序)。"""
    from langchain_core.messages import AIMessage, HumanMessage

    rows = await session.scalars(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.id != exclude_id,
            Message.role.in_(["user", "assistant"]),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    msgs = list(rows.all())[::-1]
    return [
        HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
        for m in msgs
    ]


async def compact_conversation_context(
    *,
    agent,
    registry: ModelRegistry,
    model_id: str,
    conversation: Conversation,
    session: AsyncSession,
    keep_messages: int = DEFAULT_KEEP_MESSAGES,
) -> tuple[bool, str | None, str]:
    """压缩会话上下文:生成摘要 → 清空 checkpointer → 摘要持久化到会话。

    Returns:
        (是否压缩成功, 摘要文本, 给用户看的说明)
    """
    config = {"configurable": {"thread_id": str(conversation.id)}}
    state = await agent.aget_state(config)
    msgs = (state.values or {}).get("messages") or []
    if len(msgs) < 4:
        return False, None, "对话还很短,无需压缩"

    try:
        summary = await summarize_messages(registry, model_id, msgs)
    except Exception as e:  # noqa: BLE001  摘要失败不应中断对话
        logger.warning("生成摘要失败: %s", e)
        return False, None, f"生成摘要失败:{e}"
    if not summary.strip():
        return False, None, "没有可压缩的对话内容"

    # 清空 agent 记忆,下一次对话以 摘要 + 最近几条 重新构建上下文
    saver = get_checkpointer()
    if saver is not None:
        await saver.adelete_thread(str(conversation.id))
    else:
        try:
            await agent.adelete(config)
        except Exception:  # noqa: BLE001
            pass

    conversation.summary = summary
    conversation.updated_at = func.now()
    await session.commit()
    return True, summary, f"已压缩 {len(msgs)} 条历史消息"


async def auto_compact_if_needed(
    *,
    agent,
    registry: ModelRegistry,
    model_id: str,
    conversation: Conversation,
    session: AsyncSession,
    system_prompt: str,
    context_window: int,
    trigger_fraction: float = DEFAULT_TRIGGER_FRACTION,
    keep_messages: int = DEFAULT_KEEP_MESSAGES,
) -> tuple[bool, str | None]:
    """请求前预判:上下文占用超过阈值就自动压缩(harness 的 pre-step 时机)。

    Returns:
        (是否触发压缩, 摘要文本)
    """
    if context_window <= 0:
        return False, None
    threshold = int(context_window * trigger_fraction)
    config = {"configurable": {"thread_id": str(conversation.id)}}
    try:
        state = await agent.aget_state(config)
    except Exception as e:  # noqa: BLE001  取状态失败则跳过压缩
        logger.warning("读取 agent 状态失败,跳过自动压缩: %s", e)
        return False, None

    msgs = (state.values or {}).get("messages") or []
    if not msgs:
        return False, None
    used = estimate_tokens(msgs, system_prompt)
    if used < threshold:
        return False, None

    logger.info("上下文占用 %d tokens ≥ 阈值 %d,触发自动压缩", used, threshold)
    ok, summary, note = await compact_conversation_context(
        agent=agent,
        registry=registry,
        model_id=model_id,
        conversation=conversation,
        session=session,
        keep_messages=keep_messages,
    )
    logger.info("自动压缩结果: %s (%s)", ok, note)
    return ok, summary if ok else None
