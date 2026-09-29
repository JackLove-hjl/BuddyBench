"""工具注册中心:统一注册 + 按会话上下文构建 + 携带审批元数据。

参考 deepseek-harness 的 `ToolRuntime`(register / schemas / restrict / guard)。
本项目按同样思路做一个精简版,解决三个具体问题:

1. **工具清单不再散落在建图逻辑里手写** —— 注册即生效,加工具只改一个地方;
2. **「哪些工具需要人工审批」与工具定义同处一地**(`ToolGroup.mutating`),
   不再有第二份硬编码清单需要跟工具保持同步;
3. 注册时做一致性校验(组名/工具名不重复、`mutating` 必须属于本组、工厂产出与声明一致),
   把"清单写错"变成启动即报错,而不是运行时静默缺工具。

暂未实现(harness 有、本项目当前用不到):scope 分层、`restrict()` 子代理工具裁剪、
`code` 呈现模式(只暴露 run_code 并以 SDK prompt 呈现其余工具)。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import BaseTool

from app.tools import specs
from app.tools.ask_user import make_ask_user_tools
from app.tools.code_exec import make_run_python_code
from app.tools.computer import COMPUTER_TOOL_NAME, make_computer_tool
from app.tools.context_budget import CONTEXT_TOOL_NAME, make_context_tools
from app.tools.env_wait import WAIT_TOOL_NAME, make_env_tools
from app.tools.filesystem import make_filesystem_tools
from app.tools.permissions import PERMISSION_FULL_ACCESS
from app.tools.plan import make_plan_tools
from app.tools.shell import make_run_shell_command
from app.tools.spill import make_read_spill_tool
from app.tools.web_search import make_web_tools

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    """构建工具集所需的会话上下文(对应 harness 建图时的会话事实)。"""

    permission: str
    workspace_root: str | None
    image_dir: Path
    shell_timeout: int = 60
    code_exec_timeout: int = 60
    tavily_api_key: str = ""
    # 计划模式:决定计划相关工具组是否装配(见 ToolGroup.enabled)
    plan_mode: bool = False
    # computer use(模型操作电脑):仅在「全部权限」档且开关打开时装配
    computer_use_enabled: bool = False
    computer_use_max_width: int = 1280
    computer_use_image_format: str = "jpeg"
    computer_use_jpeg_quality: int = 80
    computer_use_action_interval: float = 0.4


@dataclass(frozen=True)
class ToolGroup:
    """一组工具(由同一工厂产出)及其元数据。

    Args:
        key: 组标识(注册表内唯一)。
        tools: 该组产出的工具名,用于校验与展示。
        build: 按上下文产出工具实例的工厂。
        mutating: 组内会产生副作用(写文件/执行命令/跑代码)的工具名。
            这些工具是**人工审批清单的唯一来源**。
        enabled: 可选开关;返回 False 时该组整体不装配(如仅计划模式可用的评审工具)。
            用于"按会话状态增减工具",避免把条件判断散落回建图逻辑。
    """

    key: str
    tools: tuple[str, ...]
    build: Callable[[ToolContext], list[BaseTool]]
    mutating: tuple[str, ...] = ()
    enabled: Callable[[ToolContext], bool] | None = None


class ToolRegistry:
    """工具注册表:注册 → 校验 → 按上下文构建。"""

    def __init__(self) -> None:
        self._groups: dict[str, ToolGroup] = {}

    def register(self, group: ToolGroup) -> None:
        """注册一组工具。重复或自相矛盾的定义在注册期就抛错。"""
        if group.key in self._groups:
            raise ValueError(f"工具组重复注册:{group.key}")
        dup = set(group.tools) & self.names()
        if dup:
            raise ValueError(f"工具名重复注册:{sorted(dup)}")
        unknown = set(group.mutating) - set(group.tools)
        if unknown:
            raise ValueError(f"工具组 {group.key} 的 mutating 含未声明的工具:{sorted(unknown)}")
        self._groups[group.key] = group
        logger.debug("注册工具组 %s:%d 个工具", group.key, len(group.tools))

    def names(self) -> frozenset[str]:
        """已注册的全部工具名。"""
        return frozenset(name for group in self._groups.values() for name in group.tools)

    def groups(self) -> tuple[ToolGroup, ...]:
        """已注册的工具组(注册顺序),供一致性校验与展示使用。"""
        return tuple(self._groups.values())

    def mutating_names(self) -> tuple[str, ...]:
        """会产生副作用的工具名(人工审批清单的唯一来源)。"""
        return tuple(name for group in self._groups.values() for name in group.mutating)

    def build(self, ctx: ToolContext) -> list[BaseTool]:
        """按会话上下文实例化全部工具;产出与声明不一致时抛错(防清单漂移)。"""
        built: list[BaseTool] = []
        for group in self._groups.values():
            if group.enabled is not None and not group.enabled(ctx):
                continue  # 该组在当前会话状态下不装配(如非计划模式的评审工具)
            tools = list(group.build(ctx))
            produced = {tool.name for tool in tools}
            if produced != set(group.tools):
                raise ValueError(
                    f"工具组 {group.key} 的产出与声明不一致:"
                    f"{sorted(produced)} != {sorted(group.tools)}"
                )
            # 把 spec 里的参数说明写进 schema:`@tool` 默认不解析 docstring 的参数段,
            # 不写这一步模型侧拿到的参数就是"无说明"的(见 tools/specs.py 模块文档)。
            for tool in tools:
                specs.apply_param_docs(tool)
            built.extend(tools)
        return built


# 进程级单例(建图时读取;测试可自行构造独立实例)
registry = ToolRegistry()


def _register_builtin() -> None:
    """注册内置工具组(顺序即工具在模型侧的排列顺序)。"""
    registry.register(
        ToolGroup(
            key="shell",
            tools=("run_shell_command",),
            mutating=("run_shell_command",),
            build=lambda c: [make_run_shell_command(c.permission, c.workspace_root, c.shell_timeout)],
        )
    )
    registry.register(
        ToolGroup(
            key="filesystem",
            tools=("read_file", "write_file", "edit_file", "list_dir", "glob", "grep"),
            mutating=("write_file", "edit_file"),
            build=lambda c: list(make_filesystem_tools(c.permission, c.workspace_root)),
        )
    )
    registry.register(
        ToolGroup(
            key="code_exec",
            tools=("run_python_code",),
            mutating=("run_python_code",),
            build=lambda c: [
                make_run_python_code(
                    c.image_dir, c.code_exec_timeout, c.permission, c.workspace_root
                )
            ],
        )
    )
    registry.register(
        ToolGroup(
            key="computer",
            tools=(COMPUTER_TOOL_NAME,),
            # 真实控制鼠标键盘 → 计入审批清单(每轮首次调用挂起一次);
            # 也正因为进了 mutating,计划模式下它会自动被摘掉。
            mutating=(COMPUTER_TOOL_NAME,),
            # 只在「全部权限」档装配:它绕过工作区边界去操作整个桌面,低档位不该给
            enabled=lambda c: c.computer_use_enabled and c.permission == PERMISSION_FULL_ACCESS,
            build=lambda c: [
                make_computer_tool(
                    c.image_dir,
                    permission=c.permission,
                    enabled=c.computer_use_enabled,
                    max_width=c.computer_use_max_width,
                    image_format=c.computer_use_image_format,
                    jpeg_quality=c.computer_use_jpeg_quality,
                    action_interval=c.computer_use_action_interval,
                )
            ],
        )
    )
    registry.register(
        ToolGroup(
            key="context",
            tools=(CONTEXT_TOOL_NAME,),
            # 只读、无副作用,也没有装配前提:所有权限档位与计划模式都能用。
            # 计划阶段同样需要知道还剩多少上下文(它决定"还能探索多久")。
            build=lambda _c: list(make_context_tools()),
        )
    )
    registry.register(
        ToolGroup(
            key="env",
            tools=(WAIT_TOOL_NAME,),
            # 只读、无副作用:所有权限档位与计划模式都装配(等构建产物 / dev server 就绪)
            build=lambda _c: list(make_env_tools()),
        )
    )
    registry.register(
        ToolGroup(
            key="web",
            tools=("web_search", "web_fetch"),
            build=lambda c: list(make_web_tools(c.tavily_api_key)),
        )
    )
    registry.register(
        ToolGroup(
            key="spill",
            tools=("read_spill",),
            build=lambda _c: [make_read_spill_tool()],
        )
    )
    registry.register(
        ToolGroup(
            key="ask_user",
            tools=("ask_user",),
            # 只在计划模式装配:需求澄清是规划阶段的动作,获批实施后不该再挂起等回答。
            # 它不是副作用工具(不改任何东西),所以不写进 mutating。
            enabled=lambda c: c.plan_mode,
            build=lambda _c: list(make_ask_user_tools()),
        )
    )
    registry.register(
        ToolGroup(
            key="plan",
            tools=("exit_plan_mode",),
            # 只在计划模式装配:正常模式下评审工具对模型不可见,
            # 也就不可能出现"没在计划模式却提交计划"的误调用。
            enabled=lambda c: c.plan_mode,
            build=lambda _c: list(make_plan_tools()),
        )
    )


_register_builtin()

# 启动即校验:specs 与注册表必须一一对应。工具加了忘登记 spec、spec 指向已删除的工具、
# 组名写错,都在**导入时**报错,而不是等运行时提示词里静默少一条引导。
# (只校验内置注册表:测试可以自行构造独立 ToolRegistry 而不登记 spec。)
_SPEC_PROBLEMS = specs.validate_against(registry)
if _SPEC_PROBLEMS:
    raise ValueError("工具 spec 与注册表不一致:" + ";".join(_SPEC_PROBLEMS))
