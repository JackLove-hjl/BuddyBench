"""AgentManager:按 (model_id, permission, workspace_root, plan_mode) 构建图实例。

会话上下文在 checkpointer(thread_id=conversation_id)里,不在图实例里——所以同一会话
中途切换模型/权限/工作区,新图读同一 thread 状态即可接续。

工具集改为**注册式**(见 `app/tools/registry.py`):这里不再手写工具清单,人工审批
清单也由注册表提供(工具定义与"是否需要审批"同处一地,不会漂移)。

建图走 langchain 的 `create_agent`(见 `app/agent/graph.py`),不再经 deepagents ——
原因见 graph.py 的模块文档(内置工具注入 / 2258 字不可控提示词 / 排除手段不可用)。
"""
import logging
from collections import OrderedDict
from pathlib import Path

from app.agent.graph import build_agent
from app.agent.prompts import build_system_prompt
from app.core.registry import ModelRegistry
from app.tools.ask_user import ASK_USER_TOOL_NAME
from app.tools.computer import COMPUTER_TOOL_NAME
from app.tools.permissions import PERMISSION_FULL_ACCESS, PERMISSION_WORKSPACE_WRITABLE
from app.tools.plan import PLAN_TOOL_NAME
from app.tools.registry import ToolContext
from app.tools.registry import registry as tool_registry

logger = logging.getLogger(__name__)

AGENT_CAPACITY = 16  # 同时缓存的 agent 数(key 含模型+权限+工作区+计划模式)

# 需要人工审批的权限档位。对齐 deepseek-harness 的预设配对:
#   workspace-write    = 工作区可写 + ask   → 危险操作执行前逐次批准
#   danger-full-access = 全部权限  + never → 不再打扰("全部权限"名字即承诺,
#                                              前端在切换时已有一次性风险确认)
# 想改成在别的档位审批,只改这一行;要改审批哪些工具,改 tools/registry.py 的 mutating。
APPROVAL_PERMISSION = PERMISSION_WORKSPACE_WRITABLE


def plan_mode_hidden_tools(plan_mode: bool) -> frozenset[str]:
    """计划模式下要从工具集里摘掉的工具名;非计划模式返回空集。

    名单直接取注册表的 `mutating`(副作用工具的唯一来源),所以这里不需要维护第二份清单。
    摘掉之后模型在计划阶段只剩只读工具 + exit_plan_mode —— 提示词写不写"不要动手"它都动不了手,
    这是相对"纯提示词软引导"的硬门(实测软引导会被忽略,模型直接开写)。
    """
    return frozenset(tool_registry.mutating_names()) if plan_mode else frozenset()


class AgentManager:
    def __init__(
        self,
        registry: ModelRegistry,
        checkpointer,
        image_dir: Path,
        code_exec_timeout: int = 60,
        shell_timeout: int = 60,
        tavily_api_key: str = "",
        capacity: int = AGENT_CAPACITY,
        loop_window: int = 12,
        loop_repeats: int = 3,
        computer_use_enabled: bool = True,
        computer_use_max_actions: int = 40,
        computer_use_action_interval: float = 0.4,
        computer_use_max_width: int = 1280,
        computer_use_image_format: str = "jpeg",
        computer_use_jpeg_quality: int = 80,
        context_window_default: int = 256000,
        compact_trigger_fraction: float = 0.7,
    ):
        self._registry = registry
        self._checkpointer = checkpointer
        self._image_dir = image_dir
        self._code_exec_timeout = code_exec_timeout
        self._shell_timeout = shell_timeout
        self._tavily_api_key = tavily_api_key
        self._loop_window = loop_window
        self._loop_repeats = loop_repeats
        self._computer_use_enabled = computer_use_enabled
        self._computer_use_max_actions = computer_use_max_actions
        self._computer_use_action_interval = computer_use_action_interval
        self._computer_use_max_width = computer_use_max_width
        self._computer_use_image_format = computer_use_image_format
        self._computer_use_jpeg_quality = computer_use_jpeg_quality
        self._context_window_default = context_window_default
        self._compact_trigger_fraction = compact_trigger_fraction
        self._lru: OrderedDict[str, object] = OrderedDict()
        self._capacity = capacity
        # 已同步的 registry 配置代数(见 ModelRegistry.generation)
        self._registry_generation = -1

    @staticmethod
    def _approval_config(permission: str, plan_mode: bool) -> dict | None:
        """人工审批配置,两个来源:

        - 危险操作:仅 APPROVAL_PERMISSION 档位下挂起,清单来自工具注册表;
        - 计划评审:计划模式下 `exit_plan_mode` **始终**挂起 —— 与权限档位无关,
          只读档位下同样要"先批准计划"(对齐 harness:计划门是独立的审批轴)。
        """
        config: dict = {}
        if permission == APPROVAL_PERMISSION:
            config.update(
                {
                    name: {"allowed_decisions": ["approve", "reject"]}
                    for name in tool_registry.mutating_names()
                    # computer use 的审批由 ComputerUseMiddleware 自己做(每轮仅首次),
                    # 不走 HITL:否则同一个调用会被两处拦,还会逐次弹卡。
                    if name != COMPUTER_TOOL_NAME
                }
            )
        if plan_mode:
            config[PLAN_TOOL_NAME] = {"allowed_decisions": ["approve", "reject"]}
            # 需求澄清:用户直接作答,答案经 `respond` 决策作为工具结果回传(工具体不执行)
            config[ASK_USER_TOOL_NAME] = {"allowed_decisions": ["respond", "reject"]}
        return config or None

    def _build_tools(
        self,
        permission: str,
        workspace_root: str | None,
        plan_mode: bool,
        hidden: frozenset[str] = frozenset(),
    ) -> list:
        """按会话上下文从注册表实例化工具集(权限在工具层强制;计划模式摘掉写类工具)。"""
        tools = tool_registry.build(
            ToolContext(
                permission=permission,
                workspace_root=workspace_root,
                image_dir=self._image_dir,
                shell_timeout=self._shell_timeout,
                code_exec_timeout=self._code_exec_timeout,
                tavily_api_key=self._tavily_api_key,
                plan_mode=plan_mode,
                computer_use_enabled=self._computer_use_enabled,
                computer_use_max_width=self._computer_use_max_width,
                computer_use_image_format=self._computer_use_image_format,
                computer_use_jpeg_quality=self._computer_use_jpeg_quality,
                computer_use_action_interval=self._computer_use_action_interval,
            )
        )
        if hidden:
            tools = [tool for tool in tools if tool.name not in hidden]
        return tools

    def get_agent(
        self,
        model_id: str,
        permission: str = "read_only",
        workspace_root: str | None = None,
        plan_mode: bool = False,
    ):
        """懒构建 + LRU 缓存。key = model_id + permission + workspace_root + plan_mode,
        任一项变化都会构建新图实例(计划模式同时改变提示词与工具集)。"""
        key = f"{model_id}::{permission}::{workspace_root or ''}::{int(plan_mode)}"

        # 供应商配置变了(base_url / api_key / 模型增删)就必须整体作废:
        # 图在构建时就把 ChatOpenAI 实例固化进去了(实例内含当时的 base_url),
        # 只清 registry 的缓存没用 —— 命中下面的 _lru 时压根不会再调 get_chat_model,
        # 于是「改完配置仍旧报旧错」(例如仍打到旧的 base_url 上 404)。
        generation = self._registry.generation
        if generation != self._registry_generation:
            self._lru.clear()
            self._registry_generation = generation

        if key in self._lru:
            self._lru.move_to_end(key)
            return self._lru[key]

        model = self._registry.get_chat_model(model_id)
        hidden = plan_mode_hidden_tools(plan_mode)
        tools = self._build_tools(permission, workspace_root, plan_mode, hidden)

        # computer use / 上下文窗口 / 审批缓存三个派生根:
        # 判定条件都只写在**一个地方**(工具组、压缩阈值、审批清单),避免两边各写一份。
        computer_active = (
            self._computer_use_enabled and permission == PERMISSION_FULL_ACCESS and not plan_mode
        )
        context_window = self._registry.context_window(model_id, self._context_window_default)
        approval_tools = (
            frozenset(tool_registry.mutating_names()) - {COMPUTER_TOOL_NAME}
            if permission == APPROVAL_PERMISSION
            else frozenset()
        )

        # 动态 system prompt(persona + 工具引导 + 权限边界 [+ 计划模式]);
        # hidden 一并传入,让"没装配的工具"在提示词里也不出现
        model_name = model_id.split("::")[-1] if "::" in model_id else model_id
        system_prompt = build_system_prompt(
            model_name,
            permission,
            workspace_root,
            plan_mode,
            hidden,
            # 引导与工具装配条件必须一致(见 prompts.build_system_prompt 文档)
            computer_use_enabled=self._computer_use_enabled,
        )

        agent = build_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            checkpointer=self._checkpointer,
            image_dir=self._image_dir,
            # 指定权限档位下,危险操作执行前挂起等待人工批准/拒绝;计划模式下计划评审也挂起
            interrupt_on=self._approval_config(permission, plan_mode),
            loop_window=self._loop_window,
            loop_repeats=self._loop_repeats,
            # computer use 的**装配条件与工具组完全一致**(见 registry 的 computer 组 enabled):
            # 以前这里只看开关,于是只读/计划模式下"工具没装配但中间件还在"(虽无害但两边不一致)
            computer_use_enabled=computer_active,
            computer_use_max_actions=self._computer_use_max_actions,
            # 上下文预算:让模型能查"还剩多少",数值口径与自动压缩一致
            context_window=context_window,
            compact_trigger_fraction=self._compact_trigger_fraction,
            # 审批缓存只对"需要审批的工具"生效(与 _approval_config 同一份清单,去掉 computer)
            approval_tools=approval_tools,
            workspace_root=workspace_root,
        )
        self._lru[key] = agent
        self._lru.move_to_end(key)
        if len(self._lru) > self._capacity:
            self._lru.popitem(last=False)
        return agent
