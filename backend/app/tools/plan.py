"""计划模式工具:exit_plan_mode(把完整计划提交给用户评审)。

参考 deepseek-harness 的 `exit_plan_mode`,三个关键取舍与本项目一致:

1. **它是评审通道,不是副作用工具** —— 调用本身不改任何东西,所以它：
   - 不在 `ToolGroup.mutating` 里(那份清单是"写文件/执行命令"的审批来源);
   - 由 `manager._approval_config` 单独挂到 `interrupt_on` 上,因此**与权限档位无关**,
     只读档位下同样要走"先批准计划"。
2. **只在计划模式装配**(见 `tools/registry.py` 的 `enabled` 判定):正常模式下模型
   根本看不到这个工具,也就不会出现"没在计划模式却提交计划"的误调用。
3. **批准后的结果不回显计划全文** —— 计划本体已经作为 tool_call 参数留在上下文里,
   再回显一遍纯属浪费 token;这里只回一句"已批准,开始实施",让模型知道下一步该动手了。
"""
from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

# 计划评审的工具名(manager 挂审批、api/chat 判断"计划是否获批"共用这一处)
PLAN_TOOL_NAME = "exit_plan_mode"


def _json(status: str, **kw) -> str:
    # 与 filesystem / web_search 不同,这里刻意不走 spill:
    # 本工具的返回是几十字符的短确认,没有任何需要分页读回的正文。
    return json.dumps({"status": status, **kw}, ensure_ascii=False)


@tool
def exit_plan_mode(plan: str) -> str:
    """把完整的实施计划提交给用户评审,并在用户批准后结束计划模式。

    plan 必须是完整的 Markdown 计划,并以 `#` 标题开头(前端会按 Markdown 渲染成评审卡)。
    调用本工具会挂起当前回合,等待用户选择「批准并开始实施」或「继续修改」:
    - 批准 → 计划模式结束,你从下一步开始按计划实施,可以正常使用写文件/执行命令等工具;
    - 继续修改 → 用户反馈会作为工具结果返回,请据此修订计划并再次提交。

    计划模式下请把本工具作为该轮回复中唯一且最后一个工具调用。
    """
    text = (plan or "").strip()
    if not text:
        return _json("error", error="plan 不能为空,请提交完整的 Markdown 计划")
    if not text.startswith("#"):
        return _json(
            "error",
            error="plan 必须以 `#` 标题开头(如 `# 实施计划`),否则前端无法按 Markdown 渲染",
        )
    # 正常情况下这里已经是"用户批准后"的执行阶段:
    # 该工具在 interrupt_on 里,框架会先挂起等人工决策,批准后才真正执行本函数体。
    return _json(
        "ok",
        approved=True,
        message="计划已获用户批准,计划模式已结束。现在开始按计划实施。",
    )


def make_plan_tools() -> list[BaseTool]:
    """计划模式工具组(仅在计划模式下装配,见 tools/registry.py)。"""
    return [exit_plan_mode]
