"""System prompt 分层构建:身份 + 能力 + 工具引导 + 权限边界。

沿用 DeepSeek Harness 标准模式的 persona + 工具引导设计:
- 身份(persona):说明驱动模型与当前工作目录
- 能力:当前会话能做什么(含计划模式的裁剪)
- 安全与停止条件:异常即停、不用破坏性命令、不做计划外改动
- 工具引导:每个工具一条使用说明 —— **正文不在这里**,在 `tools/specs.py`(单一来源)
- 权限边界:明确当前权限下可用/禁用的能力
- 动态注入:cwd、permission、workspace_root 按会话解析

提示词全部使用中文,与「深度思考过程用中文」的产品约定保持一致。
"""
from __future__ import annotations

from app.tools.permissions import (
    PERMISSION_FULL_ACCESS,
    PERMISSION_READ_ONLY,
    PERMISSION_WORKSPACE_WRITABLE,
)
from app.tools.registry import registry as tool_registry
from app.tools.specs import SPECS, group_label, spec_for

# 权限中文名(前端展示与提示词共用)
PERMISSION_LABELS = {
    PERMISSION_READ_ONLY: "只读",
    PERMISSION_WORKSPACE_WRITABLE: "工作区可写",
    PERMISSION_FULL_ACCESS: "全部权限",
}

# 安全与停止条件(硬规则)。
# 这是**行为底线**,不是某个工具的用法说明,所以独立成节、紧随「能力」之后。
# 措辞参考 codex 提示词里的同类硬规则(异常即停 / 不用破坏性命令 / 不做计划外改动)。
SAFETY_RULES = """# 安全与停止条件

下面几条优先于"把任务做完":

- **看见不是你造成的改动,先停下问用户**:工作区里出现你没写过的文件或改动、内容与你预期
  不一致、本该存在的东西不见了 —— 先把看到的现象说出来让用户判断,**不要**自行删除、覆盖、
  回滚、提交或"顺手修好"。先确认那是不是自己上一轮的产物。
- **不要用破坏性命令撤改**:`git reset --hard`、`git checkout -- <文件>`、`git clean -fd`、
  `git stash drop`、`git push --force`、`rm -rf`、覆盖已存在文件的重定向等,
  一律先征得用户同意;要撤改就用保留痕迹的方式(edit_file 改回、git revert、先备份)。
- **只做用户要求的事**:不要顺手重构、重排格式、改无关文件、删掉"看起来没用"的代码,
  也不要为了让某条命令跑通去改无关配置。看到更值得做的事,在回答里说,而不是直接动手。
- **被拒绝或被拦截后不要原样重试**:工具返回权限错误、"已跳过本次调用"、"已达上限"
  这类结果,说明这条路当前走不通 —— 换个做法,或把卡点直接告诉用户。同一个调用连续失败
  两次以上必须停下来问用户,不要靠反复重试碰运气。
- **不确定就问**:宁可停下来问一个具体问题,也不要基于猜测连续动手;
  但能用代码、文档或命令查清的事实,自己查清楚,不要拿它去打扰用户。"""

# 工具引导的正文在 `tools/specs.py`(单一来源,与工具定义交叉校验);
# 本模块只负责按当前会话**实际装配的工具**把引导渲染出来 —— 见 rendered_tool_names。
# 联网能力描述(工具常驻,由模型自行判断是否使用)
WEB_CAPABILITY = (
    "当用户需要实时信息、外部资料或事实核实时,你可以联网搜索(web_search)并阅读网页(web_fetch);"
    "是否需要联网由你自行判断,不必询问用户。"
)

# 计划模式 section(对应 harness 的 `plan:policy`,order 50 —— persona 之后、工具引导之前)。
# 提示词这一层是**软引导**,只约束"先出计划、经批准再实施";真正的硬门有两道(见 manager.py):
#   1. 计划模式下写类工具**不装配**(清单取注册表的 mutating,单一来源);
#   2. exit_plan_mode 的提交必须人工审批(见 tools/plan.py 与 manager._approval_config)。
def plan_mode_section() -> str:
    """渲染计划模式 section。

    里面的工具名同样是派生的(以前是手写):只读手段取 `readonly_tool_names()`,
    "已被摘掉"取注册表的 mutating —— 与 `manager.plan_mode_hidden_tools` 同一来源,
    所以提示词说的和工具集做的必然一致。
    """
    readonly = " / ".join(readonly_tool_names())
    hidden = " / ".join(tool_registry.mutating_names())
    return f"""# 计划模式

你当前处于计划模式。在 exit_plan_mode 成功,或用户手动关闭计划模式之前,请一直保持在这个模式:
用户用命令式语气要求你实现改动时,意思是**规划**这次实现,而不是立刻执行。
本节规则优先于前文任何鼓励你写文件或执行命令的描述与引导。

- **先探索**。用只读手段({readonly},以及静态分析与检查类命令)
  把计划建立在实际代码之上。不要写文件、不要改配置、不要执行会重写已跟踪文件的格式化或代码生成、
  不要提交,也不要真的去实施计划。优先复用已有函数与既有模式,而不是新造一套机制。
- **写类工具已不在你的工具集中**({hidden}
  都不会被提供)。不要尝试调用它们,也不要因为调不了就改用**正文**交付实现 ——
  在计划通过评审之前,把完整代码、补丁或文件内容贴进回复同样算越界。
  计划阶段唯一的交付物,是通过 exit_plan_mode 提交的计划。
- 用户的对话式同意(包括回答你提出的确认问题)**不等于批准**,也不会结束计划模式;
  请把已确认的决定并入计划,再通过 exit_plan_mode 提交评审。
- 凡是能通过检查代码查明的事实请自行查明,不要询问用户代码在哪里、当前行为如何。
- 只有确实属于用户的取舍(技术栈、交付形态、范围、优先级这类代码里查不到的答案),才需要提问 ——
  并且**必须用 ask_user 提问**:每题给 2-5 个互斥选项,让用户点选或手写自定义答案。
  不要在正文里写「要 A 还是 B」:那样用户只能自由文本回复,既没有候选也没有默认推荐。
  能从代码或常识推断的,自己定,并在计划里写明假设。
- 不要把规划阶段的进度写进 todo_write:它跟踪的是计划获批之后的实施,
  计划本身属于 exit_plan_mode 的产出。
- 计划要**决策完备**:写明目标与验收标准;按子系统分组列出改动;点明公开接口、数据结构
  与数据流的变化;覆盖边界情况、失败模式、测试与验收标准,以及明确的假设。
  既要足够简洁便于评审,也要足够详细,让别人无需再做设计决策即可实施。
- 准备就绪后,调用 exit_plan_mode 提交完整计划(以 `#` 标题开头的 Markdown),
  并把该工具作为这一轮回复中**唯一且最后**的工具调用 —— 它会挂起当前回合等待用户评审。
  不要用正文直接粘贴计划,也不要问"是否继续"。
  若用户选择继续修改,他们的反馈会作为工具结果返回,请据此修订计划并再次提交。
- **exit_plan_mode 返回批准结果的那一刻,本次计划模式即告结束**:从那时起本节规则不再适用,
  请直接开始按计划实施(该用 edit_file / write_file 就用,该执行命令就执行),
  不要再提交一次计划或再次请求批准。"""


def permission_tool_names(
    permission: str, *, computer_available: bool = False
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """按权限档位算出 (可用的工具, 被权限挡住的工具)。

    工具名与"是否需要写权限"都从 `tools/specs.py` 派生,这里**不手写任何工具名**:
    以前这段文案把工具名硬编码在提示词里(如 "list_dir / glob / grep / read_file"),
    工具一改名/增删,提示词就会静默说谎,把模型引导到一个不存在的工具上。

    computer 需要额外前提(开关打开 + 全部权限),装配才列出;没装配就整条不提 ——
    写进"禁用"会让人误以为是权限不足,实情可能是开关关着。
    """
    usable: list[str] = []
    denied: list[str] = []
    for spec in SPECS:
        if spec.plan_only:
            continue  # 计划模式工具由计划模式开关决定,不属"权限"讨论范围
        if spec.requires == "computer":
            if computer_available:
                usable.append(spec.name)
            continue
        if permission == PERMISSION_READ_ONLY and spec.needs_write:
            denied.append(spec.name)
        else:
            usable.append(spec.name)
    return tuple(usable), tuple(denied)


def readonly_tool_names() -> tuple[str, ...]:
    """只读探索类工具(计划模式与能力描述里说的"只读手段")。"""
    return tuple(
        spec.name
        for spec in SPECS
        if not spec.plan_only and not spec.needs_write and spec.requires != "computer"
    )


def _format_capabilities(names: tuple[str, ...]) -> str:
    """把工具名按能力分组渲染成中文,如「读取文件(read_file / list_dir / glob / grep)」。"""
    grouped: dict[tuple[str, bool], list[str]] = {}
    for name in names:
        spec = spec_for(name)
        if spec is None:
            continue
        grouped.setdefault((spec.group, spec.needs_write), []).append(name)
    parts = [
        f"{group_label(group, needs_write=needs_write)}({' / '.join(items)})"
        for (group, needs_write), items in grouped.items()
    ]
    return "、".join(parts) if parts else "无"


def build_permission_section(
    permission: str, workspace_root: str | None, *, computer_available: bool = False
) -> str:
    """权限边界 section:明确当前会话可做/不可做。

    computer_available:computer use 在当前会话**是否真的装配**。只有它同时满足
    「开关打开 + 全部权限 + 非计划模式」时才在"可用"里写出该能力 —— 否则能力声明
    与实际工具集不一致(提示词里写了却调不到,模型会改用正文粘贴实现这类绕过方式)。
    """
    ws = workspace_root or "未设置(文件工具不可用)"
    usable, denied = permission_tool_names(permission, computer_available=computer_available)
    if permission == PERMISSION_READ_ONLY:
        denied_text = f"{_format_capabilities(denied)}(需要更高权限;只读档位下任何写操作都会被工具层拒绝)"
    elif permission == PERMISSION_WORKSPACE_WRITABLE:
        # 这一档的"禁用"是关于**路径范围**而不是某些工具,所以仍是散文描述(与工具名无关,不会漂移)
        denied_text = "访问工作区之外的路径(工具层会拦截路径穿越);写工作区外的文件"
    else:  # full_access
        denied_text = "无(全部权限)"
    return (
        f"# 权限边界\n"
        f"当前会话权限:{PERMISSION_LABELS.get(permission, permission)}。\n"
        f"工作区:{ws}\n"
        f"可用:{_format_capabilities(usable)}。\n"
        f"禁用:{denied_text}。\n"
        "请严格遵守权限边界;越权操作会被工具层拒绝并返回权限错误。"
    )


def rendered_tool_names(
    *,
    plan_mode: bool = False,
    hidden_tools: frozenset[str] = frozenset(),
    computer_available: bool = False,
) -> tuple[str, ...]:
    """当前会话下提示词里应当出现的工具名(顺序与 specs 一致)。

    判定条件必须与工具集的**实际装配**保持一致:提示词里写了却调不到,会直接诱发模型
    改用"正文粘贴实现"这类绕过方式。这条不变量由 `tests/test_tool_specs.py` 按
    (权限档位 × 计划模式 × 开关) 逐一交叉校验,不再是"写完靠人肉记住"。
    """
    return tuple(
        spec.name
        for spec in SPECS
        if not (spec.plan_only and not plan_mode)
        and spec.name not in hidden_tools
        and not (spec.requires == "computer" and not computer_available)
    )


def build_system_prompt(
    model_name: str,
    permission: str,
    workspace_root: str | None,
    plan_mode: bool = False,
    hidden_tools: frozenset[str] | None = None,
    computer_use_enabled: bool = False,
) -> str:
    """按会话参数动态构建 system prompt。

    hidden_tools:当前会话**没有装配**的工具名(计划模式下被摘掉的写类工具)。
    默认(None)按 plan_mode 从注册表的 mutating 推导,与 `manager.plan_mode_hidden_tools`
    是同一规则 —— 以前默认是空集,调用方忘了传就会渲染出"计划模式下仍列出写文件引导"
    这种**看起来正常、其实在说谎**的提示词(实测踩过),现在不传也不会出错。

    computer_use_enabled:computer use 的引导只在「开关打开 + 全部权限 + 非计划模式」
    时出现 —— 该工具按同样条件装配(见 tools/registry.py),两边必须一致。
    """
    if hidden_tools is None:
        hidden_tools = frozenset(tool_registry.mutating_names()) if plan_mode else frozenset()
    cwd = workspace_root or "未设置"
    persona = (
        f"你是一个由 {model_name} 模型驱动的编程智能体(coding agent)。"
        f"当前工作目录为 {cwd}。"
    )
    computer_available = (
        computer_use_enabled and permission == PERMISSION_FULL_ACCESS and not plan_mode
    )
    capabilities = [
        "你具备文件系统、Shell 与联网能力。",
        (
            "你可以在工作区内读写文件、执行 Shell 命令、运行 Python 绘图,并回答通用问题。"
            if not plan_mode
            else "当前处于计划模式:写类工具(写文件 / 改文件 / 执行命令 / 运行代码)不在你的工具集中,"
            "你只能用只读手段(list_dir / glob / grep / read_file)探索,最后用 exit_plan_mode 提交计划。"
        ),
        WEB_CAPABILITY,
    ]
    if computer_available:
        # 注意:必须在拼「# 能力」之前追加 —— 拼完再 append 等于没写(以前就是这个问题)
        capabilities.append(
            "在「全部权限」档下,你还能直接操作电脑的图形界面(computer:截屏 + 鼠标键盘):"
            "先截屏看清界面,再按坐标点击或输入,适用于没有 API 的任务。"
        )
    capabilities.append(
        "深度思考过程使用中文。"
        + (
            f"当用户要求修改代码时,先探索({' / '.join(readonly_tool_names())})再动手,"
            "然后用 edit_file 做精确修改,或用 run_shell_command 执行命令。"
            if not plan_mode
            else "当用户要求修改代码时,先用只读手段把事实查清,再通过 exit_plan_mode 提交计划;"
            "规划阶段不要改动任何代码。"
        )
        + "除非用户使用其他语言,否则一律用中文回复。"
    )
    sections = [persona]
    sections.append("# 能力\n" + "\n".join(capabilities))
    sections.append(SAFETY_RULES)
    sections.append("# 工具引导")
    for name in rendered_tool_names(
        plan_mode=plan_mode, hidden_tools=hidden_tools, computer_available=computer_available
    ):
        spec = spec_for(name)
        if spec is not None:
            sections.append(f"- {name}: {spec.guidance}")
    sections.append(
        build_permission_section(permission, workspace_root, computer_available=computer_available)
    )
    if plan_mode:
        # 放在权限边界之后:计划模式对"能不能动手"的约束是最后生效的那一句
        sections.append(plan_mode_section())
    sections.append(
        "# 输出要求\n"
        "- 最终回答使用 Markdown;引用生成的图片时给出完整图片 URL。\n"
        "- 不要声称自己能够生成照片 / 写实图片 —— 你只能通过代码绘制图表。"
    )
    return "\n\n".join(sections)
