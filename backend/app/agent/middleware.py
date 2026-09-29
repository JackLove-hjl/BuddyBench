"""工具层中间件:悬空工具调用修补 + computer use 审批护栏。

背景:以前这一层是 `StripBuiltinToolsMiddleware`(过滤 deepagents 注入的内置工具),
现在建图改走 langchain 的 `create_agent`(见 agent/graph.py),不再有内置工具注入,
也就不再需要那层过滤与执行侧拦截 —— 名字碰撞、`execute` 权限绕过、审批放行内置工具
这三类问题从根上消失。

现在这两个中间件各管一件事:

1. `PatchDanglingToolCallsMiddleware` —— 进模型前补齐"没有结果的工具调用";
2. `ComputerUseMiddleware` —— computer use 的审批与护栏(见下)。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage
from langgraph.types import interrupt

from app.agent import fragments
from app.services.images import image_url_to_data_url
from app.tools.computer import COMPUTER_TOOL_NAME

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# 下面两条都是**注入给模型的片段**,类型登记在 agent/fragments.py。
# 判定处一律用 fragments.contains(而不是子串比较常量):改文案时不会让
# "识别这条消息是不是我们自己写的"这件事静默失效,旧会话里的老文案也仍能识别。
MISSING_RESULT = fragments.render("tool.missing_result", "(该工具调用未执行或未返回结果,已跳过)")
REJECTED_NOTE = fragments.render("computer.rejected", "本轮已拒绝电脑操作")

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


class ComputerUseMiddleware(AgentMiddleware[Any, Any, Any]):
    """computer use 的审批与护栏。

    四件事(顺序即优先级):

    1. **每轮首次审批一次**:本轮第一次调用 `computer` 时 `interrupt()`,载荷复用 HITL 的
       `action_requests` 形状 —— 前端审批卡与 `/api/chat/approve`(`Command(resume=...)`)
       完全复用,不改接口。批准后本轮连续动作不再打断(模型操作屏幕本来就是连续动作)。
    2. **拒绝后短路**:本轮后续调用一律返回可读错误,不再执行、也不再弹卡。
    3. **动作上限**:本轮 `computer` 工具结果数达到 `max_actions` 后不再执行 —— 屏幕操作
       每步画面都不同,现有循环守卫拦不住它,这一层是必要兜底。
    4. **截图注入**:`computer` 的 screenshot 结果只有 `/images/xxx.jpg` 路径,模型看不到图;
       这里在**进模型之前**把该图片以 base64 内联成一条 user 消息(第三方网关拉不到 localhost),
       同时删掉上一条注入的截图 —— 上下文里始终只有一张最新画面,长任务不会膨胀。

    实现注意:钩子必须**同步与异步都实现**(图走异步路径,只写同步版会 NotImplementedError,
    实测踩过);审批状态不落 state 字段,而是从消息里推导(天然按会话隔离、无需清理)。
    """

    def __init__(self, *, image_dir: Path, max_actions: int = 40) -> None:
        self._image_dir = Path(image_dir)
        self._max_actions = max(1, int(max_actions))

    # ---------- 状态推导(纯函数,不新增 state 字段) ----------

    @staticmethod
    def _is_screen_message(message: Any) -> bool:
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            return False
        return any(
            isinstance(block, dict)
            and fragments.contains(str(block.get("text", "")), "computer.screen")
            for block in content
        )

    def _turn_slice(self, messages: list[Any]) -> list[Any]:
        """本轮消息:从最后一条"非注入片段"的用户消息开始(见 fragments.turn_messages)。

        注入片段(屏幕截图、本轮改动清单…)不算轮次边界 —— 否则审批计数与动作上限
        会把同一轮算成好几轮。
        """
        return fragments.turn_messages(messages)

    def _scan(self, state: Any) -> tuple[int, bool]:
        """(本轮已执行的 computer 动作数, 本轮是否已被拒绝)。"""
        messages = (state or {}).get("messages") or []
        done = 0
        rejected = False
        for message in self._turn_slice(messages):
            if isinstance(message, ToolMessage) and getattr(message, "name", "") == COMPUTER_TOOL_NAME:
                done += 1
                if fragments.contains(str(message.content), "computer.rejected"):
                    rejected = True
        return done, rejected

    # ---------- 护栏:审批 / 拒绝短路 / 动作上限 ----------

    def _error(self, tool_call_id: str, error: str) -> ToolMessage:
        return ToolMessage(
            content=json.dumps({"status": "error", "error": error}, ensure_ascii=False),
            tool_call_id=tool_call_id,
            name=COMPUTER_TOOL_NAME,
            status="error",
        )

    def _before(self, request: Any) -> ToolMessage | None:  # noqa: ANN401
        """执行前判断;返回 ToolMessage 表示不执行工具。"""
        call = getattr(request, "tool_call", None) or {}
        if str(call.get("name")) != COMPUTER_TOOL_NAME:
            return None
        state = getattr(request, "state", None) or {}
        done, rejected = self._scan(state)
        tool_call_id = str(call.get("id") or "")

        if rejected:
            return self._error(
                tool_call_id,
                f"{REJECTED_NOTE};本轮不要重复调用。如需继续,请让用户同意后重新发起。",
            )
        if done >= self._max_actions:
            return self._error(
                tool_call_id,
                f"本轮电脑操作已达上限({self._max_actions} 次),为避免失控已停止。"
                "请总结当前进展并让用户确认下一步。",
            )
        if done == 0:
            decision = interrupt(
                {
                    "action_requests": [
                        {
                            "name": COMPUTER_TOOL_NAME,
                            "args": call.get("args") or {},
                            "description": (
                                "模型请求操作你的桌面(截屏 / 鼠标 / 键盘)。"
                                "批准后本轮内的连续动作不再逐次确认;要中断请把鼠标甩到屏幕角落。"
                            ),
                        }
                    ]
                }
            )
            approved = bool(
                isinstance(decision, dict)
                and any(d.get("type") == "approve" for d in (decision.get("decisions") or []))
            )
            if not approved:
                return self._error(
                    tool_call_id, f"{REJECTED_NOTE};本轮不会再执行任何屏幕操作。"
                )
        return None

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """同步路径。"""
        short = self._before(request)
        if short is not None:
            return short
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """异步路径(图实际走这条)。"""
        short = self._before(request)
        if short is not None:
            return short
        return await handler(request)

    # ---------- 截图注入:让模型真的"看见"屏幕 ----------

    def before_model(self, state: Any, runtime: Runtime[Any]) -> dict | None:
        """同步路径。"""
        return self._screen_update(state)

    async def abefore_model(self, state: Any, runtime: Runtime[Any]) -> dict | None:
        """异步路径(图实际走这条)。"""
        return self._screen_update(state)

    def _screen_update(self, state: Any) -> dict | None:
        messages = (state or {}).get("messages") or []
        latest_url: str | None = None
        for message in messages:
            if isinstance(message, ToolMessage) and getattr(message, "name", "") == COMPUTER_TOOL_NAME:
                try:
                    data = json.loads(str(message.content))
                except (ValueError, TypeError):
                    continue
                url = data.get("image")
                if isinstance(url, str) and url:
                    latest_url = url
        if not latest_url:
            return None

        previous_id: str | None = None
        previous_url: str | None = None
        for message in reversed(messages):
            if self._is_screen_message(message):
                previous_id = str(getattr(message, "id", "") or "") or None
                for block in message.content:  # type: ignore[union-attr]
                    if isinstance(block, dict) and fragments.contains(
                        str(block.get("text", "")), "computer.screen"
                    ):
                        previous_url = str(block.get("text"))
                break
        if previous_url and latest_url in previous_url:
            return None  # 这张已经注入过,别重复

        data_url = image_url_to_data_url(latest_url, self._image_dir)
        if not data_url:
            return None  # 图片缺失/过大:静默跳过,工具结果里仍有路径可供界面显示

        update: list[Any] = []
        if previous_id:
            update.append(RemoveMessage(id=previous_id))
        update.append(
            HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": fragments.render(
                            "computer.screen",
                            f"({latest_url})这是当前屏幕;"
                            "坐标请按最近一次 computer 结果里的 coordinate_space 给。",
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
            )
        )
        logger.info("computer use 注入屏幕截图:%s", latest_url)
        return {"messages": update}
