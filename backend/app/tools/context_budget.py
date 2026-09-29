"""get_context_remaining:让模型自己看见"还剩多少上下文"。

为什么需要它:上下文压缩目前是**被动**的 —— 占用超过阈值才在请求前触发,而模型
对此一无所知,于是会一路读大文件、堆工具结果,直到某次请求直接撞上上下文上限
(表现是 provider 报超限、或压缩把关键细节抹掉)。

有了这个工具,模型可以在动手前问一句"还剩多少",据此决定:继续读、先收敛结论、
还是把长结果交给 read_spill 落盘。

实现方式与 ask_user 同构(见 tools/ask_user.py 的说明):工具本身没有逻辑 ——
它拿不到当前的消息序列,真正的计算在中间件里(`agent/middleware.ContextBudgetMiddleware`
拦截本次调用,用当前 state 现场算出结果)。因此函数体只在被绕过中间件时才会执行。
"""
from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

CONTEXT_TOOL_NAME = "get_context_remaining"


@tool
def get_context_remaining() -> str:
    """查看当前对话还剩多少上下文(已用 / 剩余 token、自动压缩阈值、现在该怎么做)。

    什么时候用:
    - 准备读取大文件、抓网页、跑会产生长输出的命令**之前**;
    - 长任务进行到一半,想确认还能不能继续;
    - 工具结果多次被 spill 落盘、感觉上下文在变紧时。

    返回:已用与剩余 token、模型上下文窗口、自动压缩阈值(超过就会压缩),
    以及一句可执行建议。它是只读的,不会改变任何状态。
    """
    # 正常路径不会走到这里:中间件会拦截本次调用并返回真实数值(见模块文档)。
    return json.dumps(
        {
            "status": "error",
            "error": "该工具由 agent 中间件应答,未能取到上下文统计;请直接继续任务。",
        },
        ensure_ascii=False,
    )


def make_context_tools() -> list[BaseTool]:
    """上下文预算工具组(所有档位、计划模式都装配:计划阶段同样需要知道预算)。"""
    return [get_context_remaining]
