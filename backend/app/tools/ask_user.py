"""计划模式工具:ask_user(用结构化选择题向用户收集需求 / 请他做取舍)。

为什么不靠"模型在正文里问":
- 正文提问在计划流程里是个死胡同 —— 用户只能自由文本回一句,颗粒度完全取决于他怎么答;
  选择题能把「候选项 + 各自取舍」直接摆出来,点一下即可;
- 结构化的问题与答案会作为工具调用 / 工具结果留在上下文里,刷新后仍可原样回放。

与 exit_plan_mode 相同的两个取舍:
1. **它没有副作用**(不改任何东西),所以不写进 `ToolGroup.mutating`(那份清单是
   "写文件 / 执行命令"的审批来源);由 `manager._approval_config` 单独挂到 `interrupt_on`,
   因此与权限档位无关。
2. **答案不来自"工具执行"** —— HITL 的 `respond` 决策会生成一条 `status="success"` 的
   合成 ToolMessage 回给模型,**函数体本身不会被执行**。所以参数校验必须落在 pydantic
   schema 上(下面的 Field 约束),写在函数体里的校验等于没有。
"""
from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

# 工具名(manager 挂审批、前端识别问题卡共用这一处)
ASK_USER_TOOL_NAME = "ask_user"

MAX_QUESTIONS = 4  # 一次最多几题(太多会变成问卷)
MAX_OPTIONS = 5  # 每题最多几个选项


class AskUserOption(BaseModel):
    """一个候选项:短标签 + 取舍说明。"""

    label: str = Field(max_length=60, description="选项短标签,如「原生 HTML/CSS/JS」")
    description: str = Field(
        default="", max_length=200, description="这项的取舍/适用场景说明(可选)"
    )


class AskUserQuestion(BaseModel):
    """一个要用户决定的问题。"""

    question: str = Field(
        max_length=300, description="要用户决定什么,一句话说清(不要加「请选择」这类废话)"
    )
    options: list[AskUserOption] = Field(
        default_factory=list,
        max_length=MAX_OPTIONS,
        description=(
            "2-5 个互斥选项;前端会自动追加一个「输入自定义回答」,所以不要自己写「其他」这类选项。"
            "留空表示这是一道只能手写的开放题"
        ),
    )


def _json(status: str, **kw) -> str:
    return json.dumps({"status": status, **kw}, ensure_ascii=False)


@tool
def ask_user(
    questions: Annotated[
        list[AskUserQuestion],
        Field(min_length=1, max_length=MAX_QUESTIONS, description="要问用户的问题(最多 4 题)"),
    ],
) -> str:
    """在你动手规划之前,用结构化选择题向用户收集需求或请他做取舍。

    什么时候该用:用户的目标里存在**必须由他决定**的取舍(技术栈、范围、兼容性、
    优先级、交付形态),而代码/文档里查不到答案。
    什么时候不该用:能从代码、文档或常识推断出来的,一律自己查、自己定(在计划里写明假设);
    用户已经说清楚的,不要再确认一遍。

    调用后当前回合会挂起,等用户在问题卡上作答:
    - 用户「完成」→ 他的答案作为工具结果返回,你据此继续完善计划;
    - 用户「跳过」→ 视为授权你自己决定,请在计划里写明你的选择与理由。

    本工具应在该轮回复中作为唯一且最后的工具调用,不要与 read_file 等混在同一轮。
    """
    # 正常路径不会走到这里:该工具挂了 HITL 中断,用户答案以 `respond` 决策生成合成
    # ToolMessage 返回。函数体只在被 approve 时才会执行,而那时并没有答案可取。
    return _json(
        "error",
        error="本工具需要用户在中断卡上作答,请勿自行假设答案",
    )


def make_ask_user_tools() -> list[BaseTool]:
    """需求澄清工具组(仅在计划模式下装配,见 tools/registry.py)。"""
    return [ask_user]
