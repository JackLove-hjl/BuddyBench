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

from app.agent import fragments
from app.agent.tool_calls import signature

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ToolCallRequest

# 交互类工具不拦:它们本来就该被反复调用(用户作答后模型会再调一次)
EXEMPT_TOOLS = frozenset({"ask_user", "exit_plan_mode"})

MAX_ARG_PREVIEW = 200
# 守卫写回的结果属于"注入片段"(见 agent/fragments.py):重复调用记 loop_guard.repeat,
# 连续点空记 loop_guard.stalled。识别一律走 fragments —— 改文案不会让判定静默失效,
# 历史里旧会话存的 "loop_guard" 结果也仍然认得出来(旧文案登记在片段的 legacy 里)。
_GUARD_KINDS = ("loop_guard.repeat", "loop_guard.stalled")

# --- computer use 专用规则 -------------------------------------------------
# 上一条规则(同名同参 + 结果相同)对电脑操作**结构性失效**:每次点击坐标都不同,
# 结果里还带着新截图,两头都判不出来。
# 但电脑操作有一条更本质的进展信号:做过动作后**画面有没有变化**。
# 工具层已经把它回传成 `screen_changed`,这里据此判定"连续点空"。
COMPUTER_TOOL_NAME = "computer"
# 只有这些动作"应该"改变画面;截图/等待是"先看清"的正确做法,不在其中
SCREEN_ACTIONS = frozenset({"click", "double_click", "right_click", "type", "keypress", "drag"})
# 连续多少次"没生效"就拦(含本次)
STALLED_ACTIONS_LIMIT = 3


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
                signatures[call_id] = signature(str(call.get("name") or ""), call.get("args"))
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
    return any(fragments.contains(text, kind) for kind in _GUARD_KINDS)


def _stalled_computer_streak(messages: list[Any], window: int) -> int:
    """最近的连续"没生效"电脑操作次数。

    只看**已执行完**的 computer 调用:结果里 action 属于 SCREEN_ACTIONS 且 screen_changed=false
    才算一次"点空";遇到截图/等待(说明模型在正确地对齐状态)、失败结果或链条断裂就停止计数。
    """
    streak = 0
    for _cid, sig, text in reversed(_recent_calls(messages, max(window, 12))):
        if not sig.startswith(f"{COMPUTER_TOOL_NAME}::") or not text or _is_guard_result(text):
            break
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            break
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            break
        if payload.get("action") not in SCREEN_ACTIONS:
            break
        if payload.get("screen_changed") is not False:
            break
        streak += 1
    return streak


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
        target = signature(name, tool_call.get("args"))
        current_id = str(tool_call.get("id") or "")
        # 排除本次调用:它的结果还没产生(空串),混进来会把"结果是否相同"的比较判坏
        prior = [
            text
            for cid, sig, text in _recent_calls(messages, self._window)
            if cid != current_id and sig == target
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
            content=fragments.tool_error("loop_guard.repeat", hint),
            tool_call_id=str(tool_call.get("id") or ""),
            name=name,
            status="error",
        )

    def _stalled_blocked(self, request: ToolCallRequest) -> ToolMessage | None:
        """computer 专用:连续点空时拦住"再点一次",强制模型先看清状态。"""
        tool_call = getattr(request, "tool_call", None) or {}
        if str(tool_call.get("name") or "") != COMPUTER_TOOL_NAME:
            return None
        args = tool_call.get("args") or {}
        action = str(args.get("action") or "").strip().lower()
        if action not in SCREEN_ACTIONS:
            return None  # 截图 / 等待是"先看清楚"的正确动作,永远放行
        state = getattr(request, "state", None) or {}
        streak = _stalled_computer_streak(state.get("messages") or [], STALLED_ACTIONS_LIMIT)
        if streak < STALLED_ACTIONS_LIMIT:
            return None
        hint = (
            f"已跳过本次 computer({action}):最近连续 {streak} 次电脑操作之后,屏幕画面**没有任何变化** —— "
            "说明这些动作都没有真正生效,原样再点一次不会得到不同结果。\n"
            "请先 action=\"screenshot\" 看清现状,并重点确认结果里的 foreground_window(前台窗口):\n"
            "1) 若前台窗口不是你要操作的应用:先点击该窗口一次(激活它),再点目标元素 —— "
            "Windows 下点击后台窗口通常只是把它切到前台;\n"
            "2) 若坐标不确定:必须以最近一次截图返回的 coordinate_space(已缩放坐标系)重新给坐标;\n"
            "3) 若目标应用响应慢:用 action=\"wait\" 等 1~2 秒再截图;\n"
            "4) 若以上都无效,不要再空点:直接说明当前卡在哪一步、需要用户做什么(例如由用户手动切窗口)。"
        )
        return ToolMessage(
            content=fragments.tool_error("loop_guard.stalled", hint),
            tool_call_id=str(tool_call.get("id") or ""),
            name=COMPUTER_TOOL_NAME,
            status="error",
        )

    # ---------- 钩子 ----------

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        """同步路径:命中守卫时直接返回提示,不执行工具。"""
        blocked = self._stalled_blocked(request)
        if blocked is not None:
            return blocked
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
        blocked = self._stalled_blocked(request)
        if blocked is not None:
            return blocked
        count = self._detect(request)
        if count is not None:
            return self._blocked(request, count)
        return await handler(request)
