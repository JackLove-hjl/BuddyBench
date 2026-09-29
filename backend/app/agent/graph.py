"""建图:用 langchain 的 `create_agent`,不再经 deepagents 的 `create_deep_agent`。

为什么换掉 create_deep_agent(都是实测结论):

1. **它会注入一整套内置工具**(ls / read_file / write_file / edit_file / glob / grep /
   execute + SubAgentMiddleware 的 task),而且这些工具是**直接注册进 ToolNode** 的 ——
   我们只能过滤"模型可见的工具列表",管不到执行侧的注册表。三个后果都踩过:
   - 计划模式下自研写类工具被摘掉,`write_file` 这个名字就只剩内置那个(参数是 `file_path`),
     模型按提示词/历史调用,拿到的是莫名其妙的 "file_path: Field required",于是反复重试;
   - 只读档位下内置 `execute`(shell)与 `task`(子代理)可被按名字调用,绕过权限门与工作区隔离;
   - 审批配置还得额外排除"未装配的工具",否则用户点"批准"放行的就是内置工具
     (表现:计划模式下没出计划、直接把代码写了)。
2. **它会无条件追加 2258 字的 `BASE_AGENT_PROMPT`**("You are a deep agent…"),其中
   "别停在半路解释、直接做"这类措辞与计划模式的硬门直接冲突 —— 项目提示词里不得不写
   "本节规则优先于前文任何鼓励你写文件或执行命令的描述与引导"来对抗它。
3. **它的官方排除手段不可用**:`_ToolExclusionMiddleware` 只按名字过滤(会连自研同名工具
   一起删掉),`excluded_middleware` 又没暴露成 create_deep_agent 的参数(只能注册 harness
   profile),必需中间件还不能移除。

换成 `create_agent` 后:**只装配我们传给它的工具**,不注入任何额外工具与提示词;
需要的能力(待办清单、审批、循环守卫、悬空调用修补)都由显式装配的中间件提供。
"""
from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, TodoListMiddleware

from app.agent.approval_cache import ApprovalCacheMiddleware
from app.agent.budget import ContextBudgetMiddleware
from app.agent.loop_guard import LoopGuardMiddleware
from app.agent.middleware import ComputerUseMiddleware, PatchDanglingToolCallsMiddleware
from app.agent.turn_changes import TurnChangesMiddleware

# 待办清单的工具引导(替换 langchain 自带的英文默认值,与项目提示词的语言保持一致)
TODO_SYSTEM_PROMPT = """## `write_todos`

你有 `write_todos` 工具,用来在执行多步任务时维护待办清单:每项是一个待办及其状态
(pending / in_progress / completed)。复杂任务用它保持进度可见,并在完成一项后立即更新;
简单任务不必使用。"""


def build_agent(
    *,
    model: Any,
    tools: list[Any],
    system_prompt: str,
    checkpointer: Any,
    image_dir: Any,
    interrupt_on: dict | None = None,
    loop_window: int = 12,
    loop_repeats: int = 3,
    computer_use_enabled: bool = False,
    computer_use_max_actions: int = 40,
    context_window: int = 0,
    compact_trigger_fraction: float = 0.7,
    approval_tools: frozenset[str] = frozenset(),
    workspace_root: str | None = None,
    name: str | None = None,
):
    """按会话上下文建图:模型 + 我们的工具集 + 显式装配的中间件。

    中间件顺序(= 由外到内):
      1. computer use 护栏 —— 每轮首次调用挂起审批、拒绝后短路、动作上限、
         并在进模型前把屏幕截图内联进去(仅在该工具装配时加入);
      2. 悬空工具调用修补 —— 进模型前把"没有结果的工具调用"补齐;
      3. 本轮改动清单 —— 进模型前注入"这一轮改了哪些文件"(有变化才注入);
      4. 循环守卫 —— 同名同参且结果相同的重复调用直接跳过并提示模型换策略;
      5. 上下文预算 —— 应答 get_context_remaining(模型可主动查剩余上下文);
      6. 待办清单 —— 提供 write_todos;
      7. 审批缓存 —— 本轮内已成功执行过的同名同参调用不再重复挂审批(仅审批开启时);
      8. 人工审批 —— 危险操作 / 计划评审 / 需求澄清的挂起点(放最内层,最后拦)。
    """
    middleware: list[Any] = []
    if computer_use_enabled:
        middleware.append(
            ComputerUseMiddleware(image_dir=image_dir, max_actions=computer_use_max_actions)
        )
    middleware.extend(
        [
            PatchDanglingToolCallsMiddleware(),
            TurnChangesMiddleware(workspace_root=workspace_root),
            LoopGuardMiddleware(window=loop_window, repeats=loop_repeats),
            ContextBudgetMiddleware(
                context_window=context_window,
                trigger_fraction=compact_trigger_fraction,
                system_prompt=system_prompt,
            ),
            TodoListMiddleware(system_prompt=TODO_SYSTEM_PROMPT),
        ]
    )
    if approval_tools:
        # 必须放在审批中间件**外面**:命中缓存时由它直接执行工具,从而跳过内层审批
        middleware.append(ApprovalCacheMiddleware(tools=approval_tools))
    if interrupt_on:
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware,
        checkpointer=checkpointer,
        name=name or "buddybench",
    )
