"""本轮改动聚合(对应 codex 的 `SharedTurnDiffTracker`):把"这一轮改了哪些文件"告诉模型。

为什么需要:一轮里可能改十几个文件,模型自己很难维持一份准确清单 —— 尤其在中途被
压缩、或工具结果被 spill 截断之后,"我刚才改过什么"就变得不可靠,于是出现重复修改、
漏改、或者把已经改好的文件又读一遍。用户侧看审批卡/工具卡也只是一串零散的 edit_file。

做法(不改动任何外部接口):
- 从本轮的工具结果里收集**成功**的 write_file / edit_file 的目标文件(结果 JSON 里有 `path`);
- 进模型前把它渲染成 `context.turn_changes` 片段注入;内容没变就不重复注入,
  变化时替换上一次注入的那条(上下文里始终只有一份最新的);
- 只读的一轮不注入任何东西;本轮没有改动时,把上一轮遗留的清单撤掉
  (否则它会看起来像"本轮改了这些文件")。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage

from app.agent import fragments
from app.agent.tool_calls import failed_result, result_payload

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# 会产生改动的工具(与注册表的 mutating 无关:那是审批清单,这里只关心"写了文件")
WRITE_TOOLS = frozenset({"write_file", "edit_file"})
CHANGES_KIND = "context.turn_changes"


def changed_files(messages: list[Any], workspace_root: str | None = None) -> tuple[str, ...]:
    """本轮成功改动的文件(按出现顺序去重,尽量给工作区相对路径)。"""
    changes: list[str] = []
    root = Path(workspace_root).expanduser().resolve() if workspace_root else None
    for message in fragments.turn_messages(messages):
        if not isinstance(message, ToolMessage):
            continue
        if str(getattr(message, "name", "")) not in WRITE_TOOLS:
            continue
        if failed_result(message):
            continue  # 失败/被拒绝的写操作不算改动(失败信息在 JSON 里,不能只看 status)
        payload = result_payload(message) or {}
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            continue
        if root is not None:
            try:
                path = Path(path).resolve().relative_to(root).as_posix()
            except ValueError:
                pass  # 工作区之外(全部权限档):保留原样的绝对路径
        if path not in changes:
            changes.append(path)
    return tuple(changes)


def render_changes(paths: tuple[str, ...]) -> str:
    """渲染成给模型看的片段正文(附一句提醒,避免它重复读回已改文件)。"""
    listed = "\n".join(f"- {path}" for path in paths)
    return (
        f"本轮到目前为止你已经修改过这些文件({len(paths)} 个):\n{listed}\n"
        "继续改动前请先确认它们的最新内容(用 read_file 看局部),不要再整篇重读或重复修改。"
    )


class TurnChangesMiddleware(AgentMiddleware[Any, Any, Any]):
    """把本轮改动清单作为 `context.turn_changes` 片段注入(有变化才注入)。"""

    def __init__(self, *, workspace_root: str | None = None) -> None:
        self._workspace_root = workspace_root

    def _update(self, state: Any) -> dict | None:
        messages = list((state or {}).get("messages") or [])
        previous = fragments.find_message(messages, CHANGES_KIND)
        paths = changed_files(messages, self._workspace_root)

        if not paths:
            if previous is None:
                return None
            # 本轮没有改动(或被压缩过):撤掉遗留的清单,别让它冒充本轮改动
            return {"messages": [RemoveMessage(id=str(getattr(previous, "id", "") or ""))]}

        body = render_changes(paths)
        if previous is not None and body in str(getattr(previous, "content", "")):
            return None  # 内容没变,不重复注入

        update: list[Any] = []
        if previous is not None:
            update.append(RemoveMessage(id=str(getattr(previous, "id", "") or "")))
        update.append(HumanMessage(content=fragments.render(CHANGES_KIND, body)))
        logger.debug("注入本轮改动清单:%d 个文件", len(paths))
        return {"messages": update}

    def before_model(self, state: Any, runtime: Runtime[Any]) -> dict | None:
        """同步路径。"""
        return self._update(state)

    async def abefore_model(self, state: Any, runtime: Runtime[Any]) -> dict | None:
        """异步路径(图实际走这条)。"""
        return self._update(state)
