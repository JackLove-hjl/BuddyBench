"""wait_for_environment:等"环境就绪"再继续(端口在听 / URL 可访问 / 文件出现)。

为什么要它:开发与部署类任务里"等某个东西起来"是高频动作,而模型以前只能靠两招 ——

- `sleep`:睡短了要重试(又多一次工具调用),睡长了白等;
- 反复执行探测命令:每次探测都是一条工具调用 + 一段输出,既慢又脏上下文。

这个工具在**一次调用**里轮询到就绪或超时,并给出可读的失败原因(卡在哪个地址)。

target 形态自动识别:
- `http://` / `https://` 开头 → 返回**非 5xx** 即算就绪(dev server 起来了但路径 404,
  对"服务可用"来说也算活着);
- `host:port` 或纯端口号 → TCP 能连上即算就绪;
- 其它 → 当作文件/目录路径,存在即算就绪。
"""
from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

from langchain_core.tools import BaseTool, tool

logger = logging.getLogger(__name__)

WAIT_TOOL_NAME = "wait_for_environment"
DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 300.0
DEFAULT_INTERVAL = 1.0
MIN_INTERVAL = 0.2
TCP_CONNECT_TIMEOUT = 2.0
HTTP_TIMEOUT = 5.0


def probe(target: str) -> tuple[bool, str]:
    """探一次目标:返回 (是否就绪, 未就绪原因)。"""
    if target.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(target, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
                status = getattr(resp, "status", 200)
                return (True, "") if status < 500 else (False, f"HTTP {status}")
        except urllib.error.HTTPError as e:
            # 4xx 说明服务活着(只是这个路径不存在);5xx 说明还没起来
            return (True, "") if e.code < 500 else (False, f"HTTP {e.code}")
        except Exception as e:  # noqa: BLE001  连不上/超时/DNS 失败都算未就绪
            return False, f"{type(e).__name__}: {e}"

    host, _, port = target.rpartition(":")
    if port.isdigit():
        try:
            with socket.create_connection((host or "127.0.0.1", int(port)), timeout=TCP_CONNECT_TIMEOUT):
                return True, ""
        except OSError as e:
            return False, f"{host or '127.0.0.1'}:{port} 未就绪({e})"

    path = Path(target).expanduser()
    return (True, "") if path.exists() else (False, f"路径不存在:{path}")


@tool
def wait_for_environment(
    target: str,
    timeout_seconds: float = DEFAULT_TIMEOUT,
    interval_seconds: float = DEFAULT_INTERVAL,
) -> str:
    """等待环境就绪:URL 可访问(非 5xx)/ host:port 可连接 / 路径已存在。

    什么时候用:启动开发服务器、构建产物、下载完成后 —— 需要等它**真的可用**再继续时。
    什么时候不该用:等待需要数分钟的长任务(请让用户知道,或先做别的);超时上限 300 秒。

    target 形态自动识别:`https://…`(要求非 5xx)、`localhost:5173` 或 `5173`(TCP 可连)、
    其它按文件/目录路径(存在即可)。调用会阻塞到就绪或超时,返回等待时长与最后一条失败原因。
    """
    try:
        timeout = min(max(float(timeout_seconds), 0.1), MAX_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    try:
        interval = min(max(float(interval_seconds), MIN_INTERVAL), 10.0)
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL

    started = time.monotonic()
    attempts = 0
    reason = ""
    while True:
        attempts += 1
        ready, reason = probe(target)
        waited = time.monotonic() - started
        if ready:
            logger.info("环境就绪:%s(等待 %.1fs,%d 次探测)", target, waited, attempts)
            return json.dumps(
                {
                    "status": "ok",
                    "target": target,
                    "waited_seconds": round(waited, 1),
                    "attempts": attempts,
                },
                ensure_ascii=False,
            )
        if waited >= timeout:
            logger.info("等待环境超时:%s(%.1fs,%d 次探测)", target, waited, attempts)
            return json.dumps(
                {
                    "status": "error",
                    "target": target,
                    "waited_seconds": round(waited, 1),
                    "attempts": attempts,
                    "error": (
                        f"等待 {timeout:g} 秒后仍未就绪,最后一条原因:{reason}。"
                        "请确认目标是否真的在启动(看它的日志/终端输出),或改用其它判断依据。"
                    ),
                },
                ensure_ascii=False,
            )
        time.sleep(min(interval, max(0.0, timeout - waited)))


def make_env_tools() -> list[BaseTool]:
    """环境就绪工具组(只读、无副作用:所有档位都装配)。"""
    return [wait_for_environment]
