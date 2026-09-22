"""工具层中间件:悬空工具调用修补。

背景:以前这一层是 `StripBuiltinToolsMiddleware`(过滤 deepagents 注入的内置工具),
现在建图改走 langchain 的 `create_agent`(见 agent/graph.py),不再有内置工具注入,
也就不再需要那层过滤与执行侧拦截 —— 名字碰撞、`execute` 权限绕过、审批放行内置工具
这三类问题从根上消失。

只留下**悬空工具调用**的修补,它有两种真实来源:

1. 计划获批后不再恢复计划图(见 services/chat_service.start_implementation):
   那次 `exit_plan_mode` 永远不会产出结果;
2. 用户中途停止生成时,最后一条 AI 消息里的工具调用也可能没有结果。

悬空调用会让部分供应商直接报错(工具调用必须紧跟工具结果),所以进模型前补齐。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

MISSING_RESULT = "(该工具调用未执行或未返回结果,已跳过)"

_TOOL_TYPE = "tool"


class PatchDanglingToolCallsMiddleware(AgentMiddleware[Any, Any, Any]):
    """进模型前,给没有结果的工具调用补一条 ToolMessage。"""

    def before_model(self, state: Any, runtime: Runtime[Any]) -> dict | None:
        """同步路径。"""
        return self._patch(state)

    async def abefore_model(self, state: Any, runtime: Runtime[Any]) -> dict | None:
        """异步路径(图实际走这条)。"""
        return self._patch(state)

    def _patch(self, state: Any) -> dict | None:
        messages = (state or {}).get("messages") or []
        pending: dict[str, str] = {}  # tool_call_id -> 工具名
        answered: set[str] = set()
        for message in messages:
            for call in getattr(message, "tool_calls", None) or []:
                call_id = str(call.get("id") or "")
                if call_id:
                    pending[call_id] = str(call.get("name") or "")
            if getattr(message, "type", "") == _TOOL_TYPE:
                answered.add(str(getattr(message, "tool_call_id", "") or ""))
        missing = [
            (call_id, name) for call_id, name in pending.items() if call_id not in answered
        ]
        if not missing:
            return None
        return {
            "messages": [
                ToolMessage(
                    content=MISSING_RESULT,
                    tool_call_id=call_id,
                    name=name or None,
                    status="error",
                )
                for call_id, name in missing
            ]
        }
