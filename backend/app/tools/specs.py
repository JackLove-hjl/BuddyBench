"""工具规格(spec):每个工具**面向模型的那份说明**的单一来源。

为什么要有这个文件(两个已经真实发生的问题):

1. **同一件事写在两处,改一处忘另一处。** 以前提示词里的工具引导在
   `agent/prompts.py::TOOL_GUIDANCE`,而工具自己的 docstring 在 `tools/*.py`,
   两边都在向模型描述同一个工具,却没有任何机制保证它们一致。

2. **参数说明根本没到模型侧。** langchain 的 `@tool` 默认只把函数 docstring 当
   *工具级*描述,不会解析里面的参数段 —— 实测 13 个工具里除 `computer` 外,
   参数 schema 的 description 全是空的(`edit_file` 的 `replace_all` 是什么语义、
   `grep` 的 `file_glob` 怎么用,模型都只能靠猜)。这里把参数说明补上,并在
   `registry.build()` 里写回 `args_schema`(见 `apply_param_docs`)。

参考 codex 的做法(`core/src/tools/handlers/*_spec.rs` 与 handler 分离、spec 自带测试):
本模块是"说明"的单一来源,执行逻辑仍在各工具里,两者用 `tests/test_tool_specs.py`
交叉校验 —— 包括"提示词里写了的工具,工具集里必须真的装配"这条不变量。

`guidance` 会出现在 system prompt 的工具引导里;`params` 会写进模型可见的工具 schema。
两者都不含执行逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, create_model


@dataclass(frozen=True)
class ToolSpec:
    """一个工具面向模型的说明。

    Args:
        name: 工具名(必须与注册表里的名字一致,由测试交叉校验)。
        group: 所属工具组(对应 `registry.ToolGroup.key`),用于一致性检查与能力分组。
        guidance: system prompt 里这一条工具引导的正文(不含 "- 名字:" 前缀)。
        params: 参数名 → 参数说明。键必须与工具 `args_schema` 的字段一一对应。
        plan_only: 是否只在计划模式装配(与注册表的 `enabled` 判定保持一致)。
        requires: 额外的装配前提,目前只有 `"computer"`(开关打开 + 全部权限)。
        needs_write: 是否需要**写权限**(只读档位下会被工具层拒绝)。
            注意它和 `required_approval`(注册表的 `mutating`)是**两件事**:
            `run_python_code` 与 `computer` 有副作用(要审批、计划模式下要摘掉),
            但绘图工具本身在只读档位可用。权限边界提示词按本字段渲染,
            实际行为由各工具内的 `permission_gate` 决定 —— 两者由测试交叉校验。
    """

    name: str
    group: str
    guidance: str
    params: tuple[tuple[str, str], ...] = ()
    plan_only: bool = False
    requires: str | None = None
    needs_write: bool = False

    def param_docs(self) -> dict[str, str]:
        return dict(self.params)


# 顺序即工具在提示词里的排列顺序(与 registry 里的注册顺序一致)
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="run_shell_command",
        group="shell",
        guidance=(
            "使用 run_shell_command 执行 Shell 命令(如 ls、pwd、cat、git、python)。"
            "工作目录为工作区根目录。命令请写成单行(可用 && 串联);"
            "输出过长会被截断,必要时用 head / grep 过滤。"
        ),
        params=(
            ("command", "要执行的命令。写成单行(多条用 && 串联);不要用会一直等输入的交互式命令。"),
        ),
        needs_write=True,
    ),
    ToolSpec(
        name="read_file",
        group="filesystem",
        guidance=(
            "读取文本文件请使用 read_file 工具,不要用 cat 之类的 Shell 命令。"
            "返回内容带行号,读取项目文件时优先使用它。"
        ),
        params=(("path", "文件路径(工作区相对路径,如 src/main.py)。"),),
    ),
    ToolSpec(
        name="write_file",
        group="filesystem",
        guidance=(
            "使用 write_file 创建新文件或整体替换文件内容。"
            "覆盖前建议先用 read_file 了解现状;父目录会自动创建。"
        ),
        params=(
            ("path", "要写入的文件路径(工作区相对路径);父目录会自动创建。"),
            ("content", "完整的新文件内容(会整体覆盖原内容)。"),
        ),
        needs_write=True,
    ),
    ToolSpec(
        name="list_dir",
        group="filesystem",
        guidance="使用 list_dir 查看工作区目录结构,返回条目名与类型(目录 / 文件)。",
        params=(("path", "要列出的目录(工作区相对路径;'.' 表示工作区根目录)。"),),
    ),
    ToolSpec(
        name="edit_file",
        group="filesystem",
        guidance=(
            "修改已有文件请用 edit_file 做精确替换:传入与原文完全一致的 old_string(含缩进)"
            "和 new_string。它比整体重写的 write_file 更安全、更省 token。"
            "若 old_string 匹配到多处,请补充上下文或设置 replace_all=True。"
        ),
        params=(
            ("path", "要修改的文件路径(工作区相对路径)。"),
            (
                "old_string",
                "要被替换的原文本,必须与文件里的内容完全一致(含缩进与换行),"
                "并带上足够上下文以保证只匹配到目标位置。",
            ),
            ("new_string", "替换后的新文本。"),
            ("replace_all", "为 True 时替换所有匹配;默认 False,此时匹配到多处会直接报错。"),
        ),
        needs_write=True,
    ),
    ToolSpec(
        name="glob",
        group="filesystem",
        guidance=(
            "使用 glob 按路径通配符定位文件(如 '*.py'、'src/*.ts'、'**/test_*.py'),"
            "适合「知道文件名但不确定位置」的场景。"
        ),
        params=(
            ("pattern", "通配符模式,如 '*.py'、'src/*.ts'、'**/test_*.py'(* 可跨目录)。"),
            ("path", "搜索起始目录(默认 '.',即工作区根目录)。"),
        ),
    ),
    ToolSpec(
        name="grep",
        group="filesystem",
        guidance=(
            "使用 grep 按正则在文件内容中搜索,返回 文件 / 行号 命中结果。"
            "用它定位函数定义或调用点,优于 Shell grep;可用 file_glob(如 '*.py')收窄范围。"
        ),
        params=(
            ("pattern", "Python 正则表达式,如 r'def main\\(' 或 'TODO|FIXME'。"),
            ("path", "搜索起始目录(默认 '.',即工作区根目录);也可直接传文件路径。"),
            ("file_glob", "按文件名收窄范围(如 '*.py');不传则搜索所有文本文件。"),
            ("max_results", "最多返回的匹配条数(默认 50,上限 200)。"),
        ),
    ),
    ToolSpec(
        name="run_python_code",
        group="code_exec",
        guidance=(
            "使用 run_python_code 运行 Python 绘图(已预装 matplotlib Agg 后端与中文字体)。"
            "用 plt.savefig('chart.png') 保存,图片 URL 会自动返回。"
        ),
        params=(
            ("code", "要执行的 Python 代码。每次调用是独立进程,不保留上一次的变量。"),
        ),
    ),
    ToolSpec(
        name="get_context_remaining",
        group="context",
        guidance=(
            "用 get_context_remaining 查看还剩多少上下文(已用/剩余 token、自动压缩阈值与建议动作)。"
            "在读取大文件、抓长网页、跑长输出命令**之前**先看一眼;它只读、无副作用。"
        ),
    ),
    ToolSpec(
        name="web_search",
        group="web",
        guidance=(
            "使用 web_search 检索实时或外部信息(如最新新闻、当前价格、官方文档),"
            "适用于超出自身知识范围或需要核实事实的场景。返回标题、链接与摘要。"
        ),
        params=(
            ("query", "搜索关键词。"),
            ("max_results", "返回结果条数(默认 6)。"),
        ),
    ),
    ToolSpec(
        name="web_fetch",
        group="web",
        guidance=(
            "使用 web_fetch 读取某个网页的正文,可传入 web_search 结果中的链接或任意已知网址。"
            "需要提供含协议(http/https)的完整 URL。"
        ),
        params=(("url", "完整网址,必须含 http/https 协议。"),),
    ),
    ToolSpec(
        name="wait_for_environment",
        group="env",
        guidance=(
            "启动开发服务器 / 构建产物 / 下载完成后,用 wait_for_environment 等它真的可用再继续:"
            "可传 URL(http 非 5xx 即就绪)、host:port(可连接即就绪)或文件路径(存在即就绪)。"
            "它一次调用内轮询,不要用 sleep 反复试探。"
        ),
        params=(
            ("target", "要等待的目标:URL、`host:port`/纯端口号,或文件/目录路径。"),
            ("timeout_seconds", "最长等待秒数(默认 60,上限 300)。"),
            ("interval_seconds", "两次探测的间隔秒数(默认 1,最小 0.2)。"),
        ),
    ),
    ToolSpec(
        name="read_spill",
        group="spill",
        guidance=(
            "工具结果过长时会以「头尾预览 + spill_id」返回(read_spill 提示),"
            "需要中间被省略的内容时用 read_spill 分页读回。"
        ),
        params=(
            ("spill_id", "结果里给出的 32 位十六进制 spill_id。"),
            ("offset", "从第几个字符开始读(默认 0)。"),
            ("limit", "本次最多返回的字符数(默认 8000,也是上限)。"),
        ),
    ),
    ToolSpec(
        name="ask_user",
        group="ask_user",
        plan_only=True,
        guidance=(
            "需要用户做取舍(技术栈、范围、优先级等代码里查不到的答案)时,用 ask_user 提结构化问题:"
            "每题给 2-5 个互斥选项,前端会渲染成可点选的问题卡(并自动补一个「输入自定义回答」)。"
            "能从代码或常识推断的一律不要问。"
        ),
        params=(
            (
                "questions",
                "要问用户的问题列表(最多 4 题);每题给 2-5 个互斥选项,"
                "选项留空表示这是只能手写的开放题。",
            ),
        ),
    ),
    ToolSpec(
        name="exit_plan_mode",
        group="plan",
        plan_only=True,
        guidance=(
            "计划模式下用 exit_plan_mode 把完整计划(Markdown,以 `#` 标题开头)提交给用户评审;"
            "调用后当前回合会挂起,等用户「批准并开始实施」或「继续修改」。"
            "它是计划模式的收尾动作,不要与其他工具调用混在同一轮里。"
        ),
        params=(("plan", "完整计划正文(Markdown,以 '#' 标题开头)。"),),
    ),
    ToolSpec(
        name="computer",
        group="computer",
        requires="computer",
        guidance=(
            "用 computer 操作电脑的图形界面(截屏 / 鼠标 / 键盘),用于没有 API 可用的任务:"
            "打开应用、点按钮、填表单、看界面上的报错。"
            "**先用 action=\"screenshot\" 看清当前屏幕**,再按结果里的 coordinate_space 给坐标(左上角为 0,0);"
            "每个动作之后都要重新截图确认结果,不要连续盲操作。\n"
            "结果里有两个必须看的信息:"
            "① `foreground_window` 是动作后的**前台窗口** —— 要操作别的应用时先确认它是你的目标;"
            "不是的话,先点一次该窗口把它激活(Windows 下点击后台窗口通常只是把它切到前台,要点第二次才真正生效),再点目标元素。"
            "② `screen_changed=false` 表示这一下**没有生效** —— 不要用同样的坐标重试:"
            "先截图看清状态,按结果里的 warning 逐条排查(窗口未激活 / 坐标不是截图坐标系 / 目标需要等待)。\n"
            "若连续两三次操作画面都毫无变化,立刻停下来:重新截图确认前台窗口与坐标;"
            "仍不行就直接说明卡在哪、需要用户做什么(例如由用户手动把目标窗口切到前台)。"
            "它只会真实操作屏幕:仅「全部权限」档可用;要中断可让用户把鼠标甩到屏幕角落(FAILSAFE)。"
        ),
        # 参数语义在工具 docstring 里写得很细(动作 → 必填参数的对应关系),但那些是
        # **散文**:实测模型侧的 schema 里 9 个参数一个说明都没有。这里按 docstring
        # 逐条落到参数上(action 决定哪些参数必填这一点,只能靠说明讲清楚)。
        params=(
            (
                "action",
                "动作:screenshot / click / double_click / right_click / move / drag / "
                "scroll / type / keypress / wait。",
            ),
            ("x", "横坐标;必须按最近一次截图返回的 coordinate_space 给(左上角为 0),不要用真实分辨率。"),
            ("y", "纵坐标;规则同 x。"),
            ("text", "要输入的文本(type 专用;中英文均可,中文走剪贴板粘贴)。"),
            (
                "keys",
                "按键列表,如 ['ctrl','c']、['enter'](keypress 专用);"
                "click / double_click / right_click / drag 时表示按住的修饰键(如 ['ctrl'])。",
            ),
            ("path", "拖拽轨迹,如 [[x1,y1],[x2,y2]](drag 专用)。"),
            ("scroll_x", "横向滚动量(正值向右、负值向左,单位为一格;scroll 专用)。"),
            ("scroll_y", "纵向滚动量(正值向上、负值向下,单位为一格;scroll 专用)。"),
            ("seconds", "等待秒数(wait 专用,最多 10 秒)。"),
        ),
    ),
)

SPEC_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in SPECS}

# 能力说明里的中文分组名。权限边界与计划模式两节的中文能力名唯一从**这里**取,
# 工具名则从 SPECS 派生 —— 这两处文案以前是手写工具名(改工具名就会静默说谎)。
#
# 分组名按「组」给,只有文件组额外按读/写拆两条(同一个组里既有只读工具也有写工具)。
# 刻意不用 (组, needs_write) 这种二元键:那样每加一个组都要把布尔值配准,
# 配错就静默退回组名(实测踩过:渲染出 "code_exec(run_python_code)")。
GROUP_LABELS: dict[str, str] = {
    "filesystem": "文件操作",
    "shell": "执行 Shell 命令",
    "code_exec": "运行代码与绘图",
    "context": "查看上下文预算",
    "env": "等待环境就绪",
    "web": "联网检索",
    "spill": "分页回读超长结果",
    "computer": "操作电脑",
    "ask_user": "结构化提问",
    "plan": "提交计划评审",
}
# 文件组按读写拆开:读/写能力在权限档位上完全不同,合成一句会让模型误判
_WRITE_LABELS: dict[str, str] = {"filesystem": "写文件"}
_READ_LABELS: dict[str, str] = {"filesystem": "读取文件"}


def group_label(group: str, *, needs_write: bool = False) -> str:
    """工具组的中文能力名(没登记的组退回组名,保证渲染出东西而不是空标签)。"""
    if needs_write and group in _WRITE_LABELS:
        return _WRITE_LABELS[group]
    if not needs_write and group in _READ_LABELS:
        return _READ_LABELS[group]
    return GROUP_LABELS.get(group, group)


def needs_write_names() -> frozenset[str]:
    """需要写权限的工具名(只读档位下会被工具层拒绝的那些)。"""
    return frozenset(spec.name for spec in SPECS if spec.needs_write)

# 计划模式专用工具(注册表的 enabled 判定与提示词裁剪共用这一处)
PLAN_ONLY_TOOLS: frozenset[str] = frozenset(spec.name for spec in SPECS if spec.plan_only)

# 需要额外前提的工具:目前只有 computer(见 registry 的 computer 组 enabled)
COMPUTER_REQUIRED_TOOLS: frozenset[str] = frozenset(
    spec.name for spec in SPECS if spec.requires == "computer"
)


def spec_for(name: str) -> ToolSpec | None:
    """按工具名取 spec;未登记返回 None。"""
    return SPEC_BY_NAME.get(name)


def ordered_names() -> tuple[str, ...]:
    """全部已登记工具名(按提示词里的排列顺序)。"""
    return tuple(spec.name for spec in SPECS)


def apply_param_docs(tool: BaseTool) -> bool:
    """把 spec 里的参数说明写进工具的 `args_schema`(模型侧真正会读到的 schema)。

    为什么要重建 schema 而不是直接改字段:langchain 是从 schema 生成请求体的,
    参数说明必须在**生成请求体时**就存在于 schema 里;而 `BaseTool.tool_call_schema`
    是缓存属性,写完 schema 还得把缓存清掉,否则模型侧看到的仍是旧结果。

    返回是否写入了 schema(没登记 spec、或 spec 没有参数说明时为 False)。
    """
    spec = spec_for(tool.name)
    if spec is None or not spec.params:
        return False
    base = tool.args_schema
    if not (isinstance(base, type) and issubclass(base, BaseModel)):
        return False
    docs = spec.param_docs()
    fields: dict[str, Any] = {}
    for name, info in base.model_fields.items():
        doc = docs.get(name)
        default = ... if info.is_required() else info.default
        if not doc:
            fields[name] = (info.annotation, default)
            continue
        field_info = Field(default, description=doc)
        # 原有约束(如 ask_user 的 min_length/max_length)在 pydantic v2 里存在 metadata 中,
        # 重建 schema 必须带着 —— 否则只为加一句说明,却把参数校验悄悄放宽了。
        field_info.metadata.extend(info.metadata)
        fields[name] = (info.annotation, field_info)
    # 名字带上工具名,便于在报错信息里看出是哪个工具的参数校验失败
    tool.args_schema = create_model(f"{base.__name__}__{tool.name}", **fields)  # type: ignore[attr-defined]
    # `tool_call_schema` 是 langchain 的 cached_property,结果缓存在实例 __dict__ 里:
    # 不清掉的话,模型侧拿到的仍是**第一次访问时**生成的旧 schema(说明等于没写)。
    # 这里把缓存当普通 dict 处理(类型存根把它标成只读映射,故经 Any 绕开静态检查)。
    cache: Any = tool.__dict__
    cache.pop("tool_call_schema", None)
    return True


def validate_against(registry) -> list[str]:
    """检查 specs 与某个工具注册表是否一致,返回问题列表(空列表 = 一致)。

    校验三件事(都是"两边各写一份"最容易漂移的地方):
      1. 注册表里的每个工具都有 spec;
      2. 每个 spec 都对应注册表里真实存在的工具;
      3. spec 声明的组名与工具实际所属的组一致。

    spec 的**参数键**是否与工具 `args_schema` 的字段对应,要实例化工具才能查,
    因此放在 `tests/test_tool_specs.py` 里按上下文逐一校验。
    """
    problems: list[str] = []
    registered = registry.names()
    group_of = {
        name: group.key for group in registry.groups() for name in group.tools
    }
    for name in sorted(registered):
        if name not in SPEC_BY_NAME:
            problems.append(f"工具 {name} 没有 spec(请在 app/tools/specs.py 登记)")
    for spec in SPECS:
        if spec.name not in registered:
            problems.append(f"spec {spec.name} 在注册表里没有对应工具")
            continue
        actual_group = group_of.get(spec.name)
        if actual_group != spec.group:
            problems.append(f"{spec.name} 的 spec 组名为 {spec.group},实际属于 {actual_group}")
    return problems
