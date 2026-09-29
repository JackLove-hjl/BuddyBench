"""审批缓存中间件:本轮内"已经成功执行过"的同名同参调用,不再重复挂起审批。

问题:人工审批(`HumanInTheLoopMiddleware`)按**工具名**拦截,所以每次调用都会问。
模型为了确认一件事连跑三次同样的 `git status`,用户就得点三次卡 —— 审批卡多了,
人就会开始"闭眼点批准",这反而降低安全性。

参考 codex 的做法(`ApprovalCacheKey` + 生命周期开关),这里取同样的两点,但把范围
收窄到**可证明安全**的一档:

    本轮内、同名同参、且**已经真的执行成功过**的调用 → 直接执行,不再问。

为什么安全:命中的调用在本轮里已经被用户批准并成功跑过一次,参数完全相同 ——
再问一次也不会得到不同答案。**没跑成的(被拒绝 / 报错)不入缓存**,所以"拒绝之后
还继续悄悄执行"这种情况不可能发生。

实现细节:审批中间件在链条**内层**,外层只能靠"不调用 handler"来短路,而我们要的是
**执行**。因此命中缓存时直接用 `request.tool` 执行工具(工具内部的权限门照常生效),
再把结果包成 ToolMessage 返回。
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from app.agent import fragments
from app.agent.tool_calls import failed_result, signature

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ToolCallRequest

logger = logging.getLogger(__name__)


def approved_signatures(messages: list[Any]) -> set[str]:
    """本轮里"已经成功执行过"的调用签名。

    只有**拿到了非错误结果**的调用才算数:
      - 被拒绝 / 报错的调用不算(否则"拒绝之后还继续执行"就成立了);
      - 还没有结果的调用(中断、崩溃)也不算。
    """
    call_signatures: dict[str, str] = {}
    succeeded: dict[str, str] = {}
    for message in fragments.turn_messages(messages):
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                call_id = str(call.get("id") or "")
                if call_id:
                    call_signatures[call_id] = signature(
                        str(call.get("name") or ""), call.get("args")
                    )
        elif isinstance(message, ToolMessage):
            call_id = str(getattr(message, "tool_call_id", "") or "")
            if call_id in call_signatures and not failed_result(message):
                succeeded[call_id] = call_signatures[call_id]
    return set(succeeded.values())


class ApprovalCacheMiddleware(AgentMiddleware[Any, Any, Any]):
    """本轮内已成功执行过的同名同参调用,直接执行、不再挂审批。"""

    def __init__(self, *, tools: frozenset[str]) -> None:
        """
        Args:
            tools: 需要审批的工具名(与 `manager._approval_config` 同一份清单:
                注册表的 mutating 去掉 computer —— 它有自己的"每轮首次"审批)。
        """
        self._tools = frozenset(tools)

    def _hit(self, request: ToolCallRequest) -> bool:
        call = getattr(request, "tool_call", None) or {}
        name = str(call.get("name") or "")
        if name not in self._tools:
            return False
        state = getattr(request, "state", None) or {}
        messages = state.get("messages") or []
        target = signature(name, call.get("args"))
        return target in approved_signatures(list(messages))

    async def _run(self, request: ToolCallRequest) -> ToolMessage:
        """直接执行工具并包装结果(不经过内层的审批中间件)。"""
        call = getattr(request, "tool_call", None) or {}
        name = str(call.get("name") or "")
        tool = getattr(request, "tool", None)
        if tool is None:  # 理论上不会发生:未注册的工具不会有审批配置
            return ToolMessage(
                content=json.dumps(
                    {"status": "error", "error": f"工具未注册,无法执行:{name}"}, ensure_ascii=False
                ),
                tool_call_id=str(call.get("id") or ""),
                name=name,
                status="error",
            )
        result = await tool.ainvoke(call.get("args") or {})
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        logger.info("审批缓存命中,本轮不再重复询问:%s", name)
        return ToolMessage(
            content=content,
            tool_call_id=str(call.get("id") or ""),
            name=name,
            status="success",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        """同步路径。"""
        if self._hit(request):
            # 同步路径不能 await:交给外层 handler 自行处理(异步路径才是图实际走的)
            return handler(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        """异步路径(图实际走这条):命中缓存就自己执行,跳过内层审批。"""
        if self._hit(request):
            return await self._run(request)
        return await handler(request)
