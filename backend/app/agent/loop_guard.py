"""循环守卫:阻断「无进展的重复工具调用」。

快模型在"结果太长被 spill → 分页读回"这类场景里容易原地打转:同一组参数反复调同一个工具,
每次拿到的结果一模一样,却推进不下去。放任下去有三个代价 —— 白烧 token、白占步数
(最后撞到 recursion_limit,用户只看到一句英文报错)、上下文里堆满重复内容。

判定放在**图中间件**层而不是事件桥接层:这里可以"不执行 + 回一条提示",模型当场就能换策略;
桥接层只能事后中止整轮。

规则(窗口 + 结果一致性,宁漏不误杀):
    最近 W 次工具调用里,同名同参的调用(含本次)达到 K 次,**且这些调用拿到的结果完全相同**
    → 再执行一次不会有任何新信息,于是跳过执行,直接回一条提示。

为什么要带"结果相同"这个条件:编辑之后再读同一个文件是**有意义**的(结果会变);
只有结果一模一样才等价于原地踏步,这样几乎不会误杀正常重读。

状态不存在中间件实例上,而是每次从 `request.state["messages"]` 现场统计,因此:
  - 天然按会话隔离(不同 thread 互不干扰),不需要 thread_id→状态的映射;
  - 进程重启、多 worker 都不会丢也不串。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ToolCallRequest

# 交互类工具不拦:它们本来就该被反复调用(用户作答后模型会再调一次)
EXEMPT_TOOLS = frozenset({"ask_user", "exit_plan_mode"})

MAX_ARG_PREVIEW = 200
_GUARD_MARKER = "loop_guard"  # 用来把"守卫自己写回的结果"与真实工具结果区分开


def _signature(name: str, args: Any) -> str:
    """调用签名:工具名 + 规范化参数(sort_keys 避免键顺序不同被判成两次不同调用)。"""
    try:
        dumped = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        dumped = repr(args)
    return f"{name}::{dumped}"


def _result_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)


def _recent_calls(messages: list[Any], window: int) -> list[tuple[str, str, str]]:
    """回看最近 window 次工具调用:返回 [(call_id, 签名, 结果文本)],按时间顺序,结果缺失记 ""。

    注意:「本次调用」通常也在状态里(AI 消息已写入、结果尚未产生),由调用方按 id 排除。
    """
    signatures: dict[str, str] = {}  # tool_call_id -> 签名
    results: dict[str, str] = {}
    order: list[str] = []
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                call_id = str(call.get("id") or "")
                if not call_id:
                    continue
                signatures[call_id] = _signature(str(call.get("name") or ""), call.get("args"))
                order.append(call_id)
        elif isinstance(message, ToolMessage):
            call_id = str(getattr(message, "tool_call_id", "") or "")
            if call_id in signatures:
                results[call_id] = _result_text(message)
    return [
        (cid, signatures[cid], results.get(cid, "")) for cid in order[-max(1, window) :]
    ]


def _is_guard_result(text: str) -> bool:
    """判断某次调用的结果是否是守卫自己写回的提示(这类结果不参与"结果是否相同"的比较)。"""
    return _GUARD_MARKER in text


class LoopGuardMiddleware(AgentMiddleware[Any, Any, Any]):
    """见模块文档:窗口内同名同参且结果相同达到阈值时,跳过执行并提示模型换策略。"""

    def __init__(self, *, window: int, repeats: int) -> None:
        self._window = max(2, window)
        self._repeats = max(2, repeats)

    # ---------- 判定 ----------

    def _detect(self, request: ToolCallRequest) -> int | None:
        """该跳过本次调用时返回"同一调用已出现次数(含本次)",否则 None。"""
        tool_call = getattr(request, "tool_call", None) or {}
        name = str(tool_call.get("name") or "")
        if not name or name in EXEMPT_TOOLS:
            return None
        state = getattr(request, "state", None) or {}
        messages = state.get("messages") or []
        signature = _signature(name, tool_call.get("args"))
        current_id = str(tool_call.get("id") or "")
        # 排除本次调用:它的结果还没产生(空串),混进来会把"结果是否相同"的比较判坏
        prior = [
            text
            for cid, sig, text in _recent_calls(messages, self._window)
            if cid != current_id and sig == signature
        ]
        count = len(prior) + 1  # +1 = 本次
        if count < self._repeats:
            return None
        # 只比较"已产生的真实结果":守卫写回的提示不算(否则拦过一次之后就再也拦不住了),
        # 悬空调用(结果为空)也不算
        real = [text for text in prior if text and not _is_guard_result(text)]
        if len(real) < self._repeats - 1 or len(set(real)) != 1:
            # 拿到的结果有差异(说明在获得新信息)或结果不足:保守放行
            return None
        return count

    def _blocked(self, request: ToolCallRequest, count: int) -> ToolMessage:
        tool_call = getattr(request, "tool_call", None) or {}
        name = str(tool_call.get("name") or "")
        try:
            preview = json.dumps(tool_call.get("args") or {}, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            preview = str(tool_call.get("args"))
        if len(preview) > MAX_ARG_PREVIEW:
            preview = preview[:MAX_ARG_PREVIEW] + "…"
        hint = (
            f"已跳过本次 {name} 调用:最近 {self._window} 次工具调用里,你用完全相同的参数调了它 "
            f"{count} 次,每次拿到的结果完全相同 —— 再执行一次不会有任何新信息。\n"
            f"参数:{preview}\n"
            "请换一种做法:缩小范围(先用 grep/glob 定位再只读需要的片段)、换用其它工具,"
            "或者直接基于已经拿到的信息往下推进。若确实缺的是只有用户才知道的信息,"
            "请直接说明你需要什么。"
        )
        return ToolMessage(
            content=json.dumps(
                {"status": "error", "reason": _GUARD_MARKER, "error": hint}, ensure_ascii=False
            ),
            tool_call_id=str(tool_call.get("id") or ""),
            name=name,
            status="error",
        )

    # ---------- 钩子 ----------

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        """同步路径:命中守卫时直接返回提示,不执行工具。"""
        count = self._detect(request)
        if count is not None:
            return self._blocked(request, count)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        """异步路径(图实际走这条):命中守卫时直接返回提示,不执行工具。"""
        count = self._detect(request)
        if count is not None:
            return self._blocked(request, count)
        return await handler(request)
