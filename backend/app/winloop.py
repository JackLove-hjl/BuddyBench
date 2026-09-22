"""Windows 专用事件循环工厂(uvicorn --loop 注入用)。

psycopg3 异步要求 SelectorEventLoop;uvicorn 0.52 在 win32 默认返回
ProactorEventLoop(绕过 policy),只能通过 loop 参数显式注入本工厂。
用法:uvicorn.run(..., loop="app.winloop:selector_loop_factory")
Linux/容器内不需要,直接用默认。
"""
import asyncio
import selectors
import sys


def selector_loop_factory():
    """零参工厂:asyncio.Runner 会以无参方式调用本函数创建事件循环。"""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()
