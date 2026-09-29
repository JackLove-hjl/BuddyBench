"""上下文片段(Context Fragment):给我们**自己注入**到模型上下文里的消息一个类型。

问题背景:为了把模型拉回正轨,系统会在对话里塞各种"它自己产生的"消息 ——
屏幕截图、循环守卫的提示、悬空调用的补丁、拒绝操作的通知、历史摘要。这些消息
以前只是**约定俗成的字符串**,谁都可以往上下文里写一段话,于是:

- 想"这条提示是不是我写的"只能靠 `some_string in content` 做子串匹配(改一次文案
  就得在所有判定点同步改,漏一处就是静默失效);
- 想按类型过滤/替换/统计这些消息时,没有任何可依赖的字段。

参考 codex 的 `ContextualUserFragment`(每个注入片段有类型 id、角色、成对标记,
并且**为老会话保留旧标记的识别能力**),这里做同样的三件事:
  1. 类型化:`kind`(如 `computer.screen`)+ 角色 + 文本标记;
  2. 可识别:`scan`/`contains`/`is_fragment` —— 同一条片段只在一个地方定义;
  3. 向后兼容:`legacy` 里登记旧文案。历史里已经存在的旧片段仍能被识别
     (老会话的 checkpointer 里存的就是旧文案,不认就等于这些判定全部失效)。

用法:
    text = fragments.render("computer.screen", "这是当前屏幕;坐标按 coordinate_space 给。")
    if fragments.contains(content, "computer.rejected"):
        ...

工具结果类片段(角色 `tool`)同时会在 JSON 里带一个 `reason` 字段,方便只解析
JSON 的地方(如循环守卫)判断来源;`tool_error` 负责统一构造这种结果。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from app.tools.computer import SCREEN_MARKER


@dataclass(frozen=True)
class FragmentKind:
    """一种注入片段的类型。

    Args:
        kind: 类型化 id(如 `computer.screen`),也是文本标记里出现的名字。
        role: 该片段以什么角色进入上下文(user / system / tool)。
        legacy: 旧版本用过的文案。历史里存的旧片段靠它识别,新增片段不需要填。
    """

    kind: str
    role: str
    legacy: tuple[str, ...] = ()

    @property
    def tag(self) -> str:
        """文本标记:放在片段正文最前面,既能被人看出是系统注入,也能被程序扫出来。"""
        return f"[ctx:{self.kind}]"

    def markers(self) -> tuple[str, ...]:
        """本类型的全部可识别标记(新标记 + 旧文案)。"""
        return (self.tag, *self.legacy)

    def reason(self) -> str:
        """工具结果 JSON 里 `reason` 字段的取值(工具类片段才有意义)。"""
        return self.kind


# 目前会注入上下文的所有片段类型(新增注入点请在这里登记,并在下面的测试里覆盖)
KINDS: tuple[FragmentKind, ...] = (
    # 屏幕截图:进模型前把最新画面内联进去,并删掉上一张(见 middleware.ComputerUseMiddleware)
    FragmentKind(kind="computer.screen", role="user", legacy=(SCREEN_MARKER,)),
    # 本轮已拒绝电脑操作:后续调用短路返回的错误
    FragmentKind(kind="computer.rejected", role="tool", legacy=("本轮已拒绝电脑操作",)),
    # 悬空工具调用的补丁结果
    FragmentKind(
        kind="tool.missing_result",
        role="tool",
        legacy=("(该工具调用未执行或未返回结果,已跳过)",),
    ),
    # 循环守卫:同名同参且结果相同的重复调用被跳过
    # 旧文案收窄成"JSON 里的 reason 字段"而不是裸词 loop_guard —— 否则 grep 到一个
    # 含 loop_guard 字样的真实结果就会被误判成守卫自己写的(守卫会因此静默失效)。
    FragmentKind(
        kind="loop_guard.repeat", role="tool", legacy=('"reason": "loop_guard"',)
    ),
    # 循环守卫:computer 连续点空(画面无变化)
    FragmentKind(
        kind="loop_guard.stalled", role="tool", legacy=('"reason": "loop_guard"',)
    ),
    # 压缩后的历史摘要(以 system 角色放回上下文)
    FragmentKind(kind="context.summary", role="system", legacy=("[先前对话摘要]",)),
    # 本轮改动清单(写类工具成功执行后聚合出来,进模型前注入;见 agent/turn_changes.py)
    FragmentKind(kind="context.turn_changes", role="user"),
)

KIND_BY_NAME: dict[str, FragmentKind] = {item.kind: item for item in KINDS}


def kind_of(kind: str) -> FragmentKind | None:
    """按名字取片段类型;未登记返回 None。"""
    return KIND_BY_NAME.get(kind)


def render(kind: str, body: str = "") -> str:
    """渲染片段正文(带上类型标记)。

    标记放在最前面:模型读到的是"这是系统注入的一段内容",而不是被伪装成用户的原话;
    出了问题时也能一眼从对话记录里认出这段是谁写的。
    """
    item = KIND_BY_NAME.get(kind)
    tag = item.tag if item else f"[ctx:{kind}]"
    return f"{tag} {body}" if body else tag


def scan(text: str) -> tuple[str, ...]:
    """扫出这段文本里命中的全部片段类型(含旧文案),按 KINDS 顺序、去重。"""
    if not text:
        return ()
    return tuple(
        item.kind for item in KINDS if any(marker in text for marker in item.markers())
    )


def contains(text: str, kind: str) -> bool:
    """这段文本是否属于某个片段类型(识别新标记与旧文案)。"""
    item = KIND_BY_NAME.get(kind)
    if item is None:
        return False
    return any(marker in (text or "") for marker in item.markers())


def is_fragment(text: str) -> bool:
    """这段文本是否是"我们自己注入的片段"(而不是真实工具结果/用户原话)。"""
    return bool(scan(text))


def fragment_reasons() -> frozenset[str]:
    """全部片段的 `reason` 取值:解析工具结果 JSON 时用来识别来源。"""
    return frozenset(item.reason() for item in KINDS)


def strip_tags(text: str) -> str:
    """去掉文本里的片段标记(保留正文),用于"把注入内容当普通文本处理"的场合。

    典型用法:生成历史摘要之前 —— 摘要正文里不该混进 `[ctx:computer.screen]` 这类
    机器标记。注意只清理**新标记**(`[ctx:...]`),旧文案(如"[先前对话摘要]")是正文的一部分,
    读起来像自然语言,保持原样。
    """
    if not text:
        return text
    result = text
    for item in KINDS:
        result = result.replace(f"{item.tag} ", "").replace(item.tag, "")
    return result


def is_fragment_message(message: Any) -> bool:
    """这条消息是否是我们注入的片段(纯文本或多媒体块里带标记即可)。"""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return is_fragment(content)
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and is_fragment(str(block.get("text", "")))
            for block in content
        )
    return False


def turn_messages(messages: list[Any]) -> list[Any]:
    """本轮消息:从最后一条"非注入片段"的用户消息开始。

    注入片段(屏幕截图、本轮改动清单…)不算轮次边界 —— 否则它们会把一轮切成好几段,
    审批缓存、动作计数、改动聚合都会算错。
    """
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, HumanMessage) and not is_fragment_message(message):
            return messages[index:]
    return messages


def find_message(messages: list[Any], kind: str) -> Any | None:
    """找出最后一条指定类型的注入片段消息(用于替换或删除上一次注入)。"""
    for message in reversed(messages):
        if not is_fragment_message(message):
            continue
        content = getattr(message, "content", None)
        if isinstance(content, list):
            text = " ".join(
                str(block.get("text", "")) for block in content if isinstance(block, dict)
            )
        else:
            text = str(content)
        if contains(text, kind):
            return message
    return None


def tool_error(kind: str, error: str, *, tag_body: bool = True, **extra: object) -> str:
    """构造一条"片段式"的工具结果 JSON(带 status/reason,便于识别与统计)。

    tag_body=True(默认)会把类型标记写进 error 正文 —— 解析 JSON 的地方靠 `reason`
    识别,只做文本匹配的地方(如循环守卫自身)也能靠标记识别,两条路都通。
    """
    item = KIND_BY_NAME.get(kind)
    payload: dict[str, object] = {
        "status": "error",
        "reason": item.reason() if item else kind,
        "error": render(kind, error) if tag_body else error,
        **extra,
    }
    return json.dumps(payload, ensure_ascii=False)
