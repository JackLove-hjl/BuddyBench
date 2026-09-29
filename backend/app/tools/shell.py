"""run_shell_command 工具:在工作区根目录执行 shell 命令(沙箱化)。

安全边界:
- 权限门控:read_only 拒绝;workspace_writable / full 允许(仅限工作区 cwd)
- 超时 + 进程树清理;stdout/stderr 截断;并发信号量限流
- env 剔除含 KEY/TOKEN/SECRET 的变量
- 工作区可写权限强制 cwd=workspace_root(命令本身仍可写工作区任意处,但 shell 语义
  以 cwd 为根;越界文件访问由系统权限控制——个人学习项目可接受)
"""
import asyncio
import logging
import os
import signal
import subprocess
import sys

import psutil
from langchain_core.tools import tool

from app.tools import spill
from app.tools.permissions import PermissionDenied, permission_gate, resolve_workspace_path

logger = logging.getLogger(__name__)

# 保留的 stdout/stderr 上限:超过部分由 spill 落盘并以「头尾预览 + spill_id」呈现,
# 不再像静态截断那样把中间内容永久丢掉(模型可用 read_spill 读回)
OUTPUT_LIMIT = 200_000

_shell_semaphore = asyncio.Semaphore(2)  # 并发限流
_proc: subprocess.Popen | None = None


def _scrub_env() -> dict:
    return {
        k: v
        for k, v in os.environ.items()
        if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET"))
    }


def _kill_process_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            for child in psutil.Process(proc.pid).children(recursive=True):
                child.kill()
        if proc.poll() is None:
            proc.kill()
    except (psutil.NoSuchProcess, ProcessLookupError, PermissionError):
        pass


def make_run_shell_command(permission: str, workspace_root: str | None, timeout: int = 60):
    """返回绑定权限/工作区的 @tool(工厂,每次请求按会话配置重建)。"""

    @tool
    def run_shell_command(command: str) -> str:
        """执行 Shell 命令。工作目录为工作区根目录;可执行 ls/cat/pwd 等查询,也可运行构建脚本。

        - 只读权限下不可用(会返回权限错误)。
        - 请把命令写为单条(可用 && 串联);不支持交互式命令。
        - 输出会截断,过长输出请自行过滤(grep/head)。
        """
        # 权限门与 cwd 解析必须在 try 里:它们也会抛 PermissionDenied(只读档位、路径越界),
        # 放在外面会让异常直接冒出去 —— 而 docstring 承诺的是"返回权限错误"。
        # (实测踩过:三个写类工具里只有这个会抛异常,模型侧看到的是异常而不是可读原因)
        try:
            permission_gate(permission, need_shell=True)
            # workspace_writable 强制 cwd 落在工作区内
            cwd: str | None = None
            if workspace_root:
                cwd = str(resolve_workspace_path(".", workspace_root))
        except PermissionDenied as e:
            return json_result("error", exit_code=None, error=e.message)

        global _proc

        async def _run():
            global _proc
            async with _shell_semaphore:
                _proc = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=_scrub_env(),
                    **({"start_new_session": True} if os.name == "posix" else {}),
                )
                try:
                    return await asyncio.to_thread(_proc.communicate, input=None, timeout=timeout)
                except subprocess.TimeoutExpired:
                    _kill_process_tree(_proc)
                    raise TimeoutError(f"命令执行超过 {timeout} 秒,已终止")

        try:
            stdout, stderr = asyncio.run(_run())
        except TimeoutError as e:
            return json_result("error", exit_code=None, error=str(e))
        except PermissionDenied as e:
            return json_result("error", exit_code=None, error=e.message)
        except Exception as e:  # noqa: BLE001
            return json_result("error", exit_code=None, error=f"执行失败:{e}")

        code = _proc.returncode if _proc is not None else -1
        stdout = (stdout or "")[-OUTPUT_LIMIT:]
        stderr = (stderr or "")[-OUTPUT_LIMIT:]
        if code == 0:
            return json_result("ok", exit_code=0, stdout=stdout or None, error=None)
        return json_result(
            "error",
            exit_code=code,
            stdout=stdout or None,
            error=stderr or f"退出码 {code}",
        )

    return run_shell_command


def json_result(status: str, exit_code: int | None, stdout: str | None = None, error: str | None = None) -> str:
    """结果序列化:超长 stdout/stderr 自动 spill(信息不丢,可用 read_spill 读回)。"""
    return spill.dumps(
        {"status": status, "exit_code": exit_code, "stdout": stdout, "error": error},
        kind="shell",
    )
