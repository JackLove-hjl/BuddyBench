"""System prompt 分层构建:身份 + 能力 + 工具引导 + 权限边界。

沿用 DeepSeek Harness 标准模式的 persona + 工具引导设计:
- 身份(persona):说明驱动模型与当前工作目录
- 工具引导:每个工具一条使用说明(参考 Harness read/write/shell 措辞)
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

# 只在计划模式装配的工具(见 tools/registry.py 的 enabled 判定)。
# 非计划模式下这些工具根本不在工具集里,引导也不该出现 —— 否则模型会照着引导去调一个不存在的工具。
PLAN_ONLY_TOOLS = frozenset({"ask_user", "exit_plan_mode"})

# 权限中文名(前端展示与提示词共用)
PERMISSION_LABELS = {
    PERMISSION_READ_ONLY: "只读",
    PERMISSION_WORKSPACE_WRITABLE: "工作区可写",
    PERMISSION_FULL_ACCESS: "全部权限",
}

# 工具引导 section(order 100-199),参考 Harness read/write/shell 措辞
TOOL_GUIDANCE = {
    "run_shell_command": (
        "使用 run_shell_command 执行 Shell 命令(如 ls、pwd、cat、git、python)。"
        "工作目录为工作区根目录。命令请写成单行(可用 && 串联);"
        "输出过长会被截断,必要时用 head / grep 过滤。"
    ),
    "read_file": (
        "读取文本文件请使用 read_file 工具,不要用 cat 之类的 Shell 命令。"
        "返回内容带行号,读取项目文件时优先使用它。"
    ),
    "write_file": (
        "使用 write_file 创建新文件或整体替换文件内容。"
        "覆盖前建议先用 read_file 了解现状;父目录会自动创建。"
    ),
    "list_dir": (
        "使用 list_dir 查看工作区目录结构,返回条目名与类型(目录 / 文件)。"
    ),
    "edit_file": (
        "修改已有文件请用 edit_file 做精确替换:传入与原文完全一致的 old_string(含缩进)"
        "和 new_string。它比整体重写的 write_file 更安全、更省 token。"
        "若 old_string 匹配到多处,请补充上下文或设置 replace_all=True。"
    ),
    "glob": (
        "使用 glob 按路径通配符定位文件(如 '*.py'、'src/*.ts'、'**/test_*.py'),"
        "适合「知道文件名但不确定位置」的场景。"
    ),
    "grep": (
        "使用 grep 按正则在文件内容中搜索,返回 文件 / 行号 命中结果。"
        "用它定位函数定义或调用点,优于 Shell grep;可用 file_glob(如 '*.py')收窄范围。"
    ),
    "run_python_code": (
        "使用 run_python_code 运行 Python 绘图(已预装 matplotlib Agg 后端与中文字体)。"
        "用 plt.savefig('chart.png') 保存,图片 URL 会自动返回。"
    ),
    "web_search": (
        "使用 web_search 检索实时或外部信息(如最新新闻、当前价格、官方文档),"
        "适用于超出自身知识范围或需要核实事实的场景。返回标题、链接与摘要。"
    ),
    "web_fetch": (
        "使用 web_fetch 读取某个网页的正文,可传入 web_search 结果中的链接或任意已知网址。"
        "需要提供含协议(http/https)的完整 URL。"
    ),
    "ask_user": (
        "需要用户做取舍(技术栈、范围、优先级等代码里查不到的答案)时,用 ask_user 提结构化问题:"
        "每题给 2-5 个互斥选项,前端会渲染成可点选的问题卡(并自动补一个「输入自定义回答」)。"
        "能从代码或常识推断的一律不要问。"
    ),
    "exit_plan_mode": (
        "计划模式下用 exit_plan_mode 把完整计划(Markdown,以 `#` 标题开头)提交给用户评审;"
        "调用后当前回合会挂起,等用户「批准并开始实施」或「继续修改」。"
        "它是计划模式的收尾动作,不要与其他工具调用混在同一轮里。"
    ),
}

# 联网能力描述(工具常驻,由模型自行判断是否使用)
WEB_CAPABILITY = (
    "当用户需要实时信息、外部资料或事实核实时,你可以联网搜索(web_search)并阅读网页(web_fetch);"
    "是否需要联网由你自行判断,不必询问用户。"
)

# 计划模式 section(对应 harness 的 `plan:policy`,order 50 —— persona 之后、工具引导之前)。
# 提示词这一层是**软引导**,只约束"先出计划、经批准再实施";真正的硬门有两道(见 manager.py):
#   1. 计划模式下写类工具**不装配**(清单取注册表的 mutating,单一来源);
#   2. exit_plan_mode 的提交必须人工审批(见 tools/plan.py 与 manager._approval_config)。
PLAN_MODE_SECTION = """# 计划模式

你当前处于计划模式。在 exit_plan_mode 成功,或用户手动关闭计划模式之前,请一直保持在这个模式:
用户用命令式语气要求你实现改动时,意思是**规划**这次实现,而不是立刻执行。
本节规则优先于前文任何鼓励你写文件或执行命令的描述与引导。

- **先探索**。用只读手段(list_dir / glob / grep / read_file,以及静态分析与检查类命令)
  把计划建立在实际代码之上。不要写文件、不要改配置、不要执行会重写已跟踪文件的格式化或代码生成、
  不要提交,也不要真的去实施计划。优先复用已有函数与既有模式,而不是新造一套机制。
- **写类工具已不在你的工具集中**(write_file / edit_file / run_shell_command / run_python_code
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


def build_permission_section(permission: str, workspace_root: str | None) -> str:
    """权限边界 section:明确当前会话可做/不可做。"""
    ws = workspace_root or "未设置(文件工具不可用)"
    if permission == PERMISSION_READ_ONLY:
        allowed = "读取工作区/项目文件(list_dir / glob / grep / read_file)、运行绘图(run_python_code)"
        denied = "写文件(write_file / edit_file)、执行 Shell 命令(run_shell_command)"
    elif permission == PERMISSION_WORKSPACE_WRITABLE:
        allowed = "工作区内读写文件(list_dir / glob / grep / read_file / write_file / edit_file)、在工作区根目录执行 Shell 命令(run_shell_command)、运行绘图"
        denied = "访问工作区之外的路径(工具层会拦截路径穿越);写工作区外文件"
    else:  # full_access
        allowed = "读写任意路径文件(list_dir / glob / grep / read_file / write_file / edit_file)、执行任意 Shell 命令、运行绘图"
        denied = "无(全部权限)"
    return (
        f"# 权限边界\n"
        f"当前会话权限:{PERMISSION_LABELS.get(permission, permission)}。\n"
        f"工作区:{ws}\n"
        f"可用:{allowed}。\n"
        f"禁用:{denied}。\n"
        "请严格遵守权限边界;越权操作会被工具层拒绝并返回权限错误。"
    )


def build_system_prompt(
    model_name: str,
    permission: str,
    workspace_root: str | None,
    plan_mode: bool = False,
    hidden_tools: frozenset[str] = frozenset(),
) -> str:
    """按会话参数动态构建 system prompt。

    hidden_tools:当前会话**没有装配**的工具名(计划模式下被摘掉的写类工具)。
    能力描述与工具引导都要按它裁剪 —— 提示词里写了却调不到,会直接诱发模型
    改用"正文粘贴实现"这类绕过方式。
    """
    cwd = workspace_root or "未设置"
    persona = (
        f"你是一个由 {model_name} 模型驱动的编程智能体(coding agent)。"
        f"当前工作目录为 {cwd}。"
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
    capabilities.append(
        "深度思考过程使用中文。"
        + (
            "当用户要求修改代码时,先探索(list_dir / glob / grep / read_file)再动手,"
            "然后用 edit_file 做精确修改,或用 run_shell_command 执行命令。"
            if not plan_mode
            else "当用户要求修改代码时,先用只读手段把事实查清,再通过 exit_plan_mode 提交计划;"
            "规划阶段不要改动任何代码。"
        )
        + "除非用户使用其他语言,否则一律用中文回复。"
    )
    sections = [persona]
    sections.append("# 能力\n" + "\n".join(capabilities))
    sections.append("# 工具引导")
    for tool, guidance in TOOL_GUIDANCE.items():
        if tool in PLAN_ONLY_TOOLS and not plan_mode:
            continue  # 非计划模式不装配这些工具,引导也不出现
        if tool in hidden_tools:
            continue  # 计划模式摘掉的写类工具:引导同步消失,保证"写了就能调"
        sections.append(f"- {tool}: {guidance}")
    sections.append(build_permission_section(permission, workspace_root))
    if plan_mode:
        # 放在权限边界之后:计划模式对"能不能动手"的约束是最后生效的那一句
        sections.append(PLAN_MODE_SECTION)
    sections.append(
        "# 输出要求\n"
        "- 最终回答使用 Markdown;引用生成的图片时给出完整图片 URL。\n"
        "- 不要声称自己能够生成照片 / 写实图片 —— 你只能通过代码绘制图表。"
    )
    return "\n\n".join(sections)
