"""上下文预算中间件:应答 `get_context_remaining`。

为什么需要模型看得见预算:压缩目前是**被动**的(占用超过阈值才在请求前触发),
模型对这种压力一无所知 —— 它会一路读大文件、堆工具结果,直到某次请求撞上上限,
然后压缩把所有细节换成一段摘要。有了这个工具,模型可以在动手前问一句"还剩多少"。

实现方式与 ask_user 同构(见 tools/context_budget.py):工具本身拿不到消息序列,
所以由中间件在调用时用当前 state 现场计算并直接返回结果 —— 工具函数体不会被真正执行。
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from app.services.compaction import estimate_tokens
from app.tools.context_budget import CONTEXT_TOOL_NAME

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ToolCallRequest

logger = logging.getLogger(__name__)

# 剩余比例低于它就算"紧":即便没到压缩阈值,也该提醒模型少读大文件
LOW_REMAINING_FRACTION = 0.2


class ContextBudgetMiddleware(AgentMiddleware[Any, Any, Any]):
    """计算并回答"还剩多少上下文"。"""

    def __init__(
        self,
        *,
        context_window: int,
        trigger_fraction: float = 0.7,
        system_prompt: str = "",
    ) -> None:
        """
        Args:
            context_window: 当前模型的上下文窗口(0 表示未知)。
            trigger_fraction: 自动压缩阈值比例(与 services/compaction 一致)。
            system_prompt: 本会话的 system prompt,计入已用 token(它占的份额不小)。
        """
        self._window = max(0, int(context_window))
        self._trigger = min(max(float(trigger_fraction), 0.0), 1.0)
        self._system_prompt = system_prompt or ""

    # ---------- 计算 ----------

    def report(self, messages: list[Any]) -> dict:
        """当前占用情况 + 一句可执行建议(纯函数,便于测试)。"""
        used = estimate_tokens(messages, self._system_prompt)
        window = self._window
        threshold = int(window * self._trigger) if window > 0 else 0
        remaining = max(0, window - used) if window > 0 else 0
        return {
            "status": "ok",
            "model_context_window": window,
            "used_tokens": used,
            "remaining_tokens": remaining,
            "used_percent": round(used / window * 100, 1) if window > 0 else None,
            "auto_compact_threshold_tokens": threshold,
            "messages_in_context": len(messages),
            "advice": self._advice(used, remaining, threshold),
        }

    def _advice(self, used: int, remaining: int, threshold: int) -> str:
        if self._window <= 0:
            return (
                "该模型没配置上下文窗口,无法判断剩余量:请保守使用 —— "
                "少整篇读大文件,长输出交给 read_spill 分页读回。"
            )
        if threshold and used >= threshold:
            return (
                "上下文已达到自动压缩阈值:下一次请求很可能触发压缩(中间细节会被摘要替代)。"
                "现在请尽快收敛:把关键结论写进回复或文件,别再整篇读取大文件或抓长网页。"
            )
        if remaining < self._window * LOW_REMAINING_FRACTION:
            return "剩余上下文不多:只读必要片段(用 grep 定位行号再读局部),避免把长输出灌进上下文。"
        return "上下文充裕,可以继续当前做法。"

    # ---------- 拦截 ----------

    def _answer(self, request: ToolCallRequest) -> ToolMessage | None:
        call = getattr(request, "tool_call", None) or {}
        if str(call.get("name")) != CONTEXT_TOOL_NAME:
            return None
        state = getattr(request, "state", None) or {}
        messages = state.get("messages") or []
        report = self.report(list(messages))
        logger.debug(
            "上下文预算询问:已用 %s / %s", report["used_tokens"], report["model_context_window"]
        )
        return ToolMessage(
            content=json.dumps(report, ensure_ascii=False),
            tool_call_id=str(call.get("id") or ""),
            name=CONTEXT_TOOL_NAME,
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        """同步路径。"""
        answer = self._answer(request)
        return answer if answer is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        """异步路径(图实际走这条)。"""
        answer = self._answer(request)
        return answer if answer is not None else await handler(request)
