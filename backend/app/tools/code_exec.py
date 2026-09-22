"""run_python_code 绘图工具:子进程执行 matplotlib 代码,返回结构化 JSON。

安全边界(个人学习项目可接受,已做三层缓解):
- 超时 + 进程树清理;
- env 剔除所有含 KEY/TOKEN/SECRET 的变量,代码拿不到任何密钥;
- stdout/stderr 截断 8KB。
"""
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import psutil
from langchain_core.tools import tool

from app.tools import spill
from app.tools.permissions import PermissionDenied, permission_gate, resolve_workspace_path

logger = logging.getLogger(__name__)

# 保留的 stdout/stderr 上限:超出部分由 spill 落盘并以「头尾预览 + spill_id」呈现,
# 不再静态截断丢内容(模型可用 read_spill 读回)
OUTPUT_LIMIT = 200_000


def _dump(payload: dict) -> str:
    """结果序列化:超长字段自动 spill(信息不丢)。"""
    return spill.dumps(payload, kind="code")


def _build_prelude(image_dir: Path) -> str:
    """预置头:无窗口后端 + 中文字体兜底 + savefig 重定向到图片缓存目录。

    当会话设置了工作区时,cwd 为工作区根目录,LLM 直接 plt.savefig("x.png")
    会把图写进工作区,导致图片无法回传。这里把 savefig 的相对路径重定向到
    图片缓存目录,保证图片能被识别并返回给前端。
    """
    img_dir = str(image_dir.resolve())
    return f"""\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import os as _os
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "SimHei", "Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

_orig_savefig = plt.savefig

def _savefig(fname, *args, **kwargs):
    if isinstance(fname, str) and not _os.path.isabs(fname):
        fname = _os.path.join(r"{img_dir}", fname)
    return _orig_savefig(fname, *args, **kwargs)
plt.savefig = _savefig
"""

_semaphore = asyncio.Semaphore(2)  # 并发限流
_proc: subprocess.Popen | None = None  # 当前执行中的子进程(用于超时清理)


def _scrub_env() -> dict:
    """剔除密钥类环境变量,代码执行环境看不到任何 API key。"""
    return {
        k: v
        for k, v in os.environ.items()
        if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET"))
    }


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """尽力清理进程树(Windows 用 psutil,Linux 用 setsid+killpg)。"""
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


def _latest_png(image_dir: Path) -> Path | None:
    pngs = sorted(image_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return pngs[0] if pngs else None


def make_run_python_code(
    image_dir: Path,
    timeout: int = 60,
    permission: str = "read_only",
    workspace_root: str | None = None,
):
    """返回绑定 image_dir / 权限 / 工作区的 @tool(工厂,每次请求按会话配置重建)。"""

    @tool
    def run_python_code(code: str) -> str:
        """执行 Python 代码绘图(matplotlib 已预置 Agg 后端与中文字体)。

        绘图请用 plt.savefig("chart.png") 保存到当前目录,不要 plt.show()。
        只需保存一个文件,执行后会自动返回图片。
        只读权限下仍允许(仅写图片缓存目录);工作区可写时会设置 cwd 为工作区根目录。
        """
        try:
            permission_gate(permission, need_write=False, need_shell=False)
            # cwd:工作区可写/full 且设置了工作区 → 工作区根目录;否则用图片缓存目录
            cwd = str(resolve_workspace_path(".", workspace_root)) if (workspace_root and permission != "read_only") else str(image_dir)
        except PermissionDenied as e:
            return _dump({"status": "error", "error": e.message, "image": None})

        global _proc
        image_dir.mkdir(parents=True, exist_ok=True)
        # 记录执行前已有的 png(执行后只认新生成的,避免误删/误取历史图片)
        before = {p.name for p in image_dir.glob("*.png")}

        wrapped = _build_prelude(image_dir) + "\n" + code

        async def _run():
            global _proc
            async with _semaphore:
                _proc = subprocess.Popen(
                    [sys.executable, "-u", "-c", wrapped],
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
                    raise TimeoutError(f"代码执行超过 {timeout} 秒,已终止")

        try:
            stdout, stderr = asyncio.run(_run())
        except TimeoutError as e:
            return _dump({"status": "error", "error": str(e), "image": None})
        except Exception as e:  # noqa: BLE001
            return _dump({"status": "error", "error": str(e), "image": None})

        stdout = (stdout or "")[-OUTPUT_LIMIT:]
        stderr = (stderr or "")[-OUTPUT_LIMIT:]

        # 只认本次执行新生成的 png(savefig 重定向后一定落在 image_dir)
        png = next(
            (p for p in sorted(image_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True) if p.name not in before),
            None,
        )
        if png is None:
            png = _latest_png(image_dir)
        if png is not None:
            new_name = image_dir / f"{uuid.uuid4()}.png"
            os.replace(png, new_name)
            return _dump(
                {
                    "status": "ok",
                    "image": f"/images/{new_name.name}",
                    "stdout": stdout or None,
                    "error": None,
                }
            )
        return _dump(
            {
                "status": "error",
                "error": stderr or "代码未产生 PNG 图片(请用 plt.savefig 保存)",
                "stdout": stdout or None,
                "image": None,
            }
        )

    return run_python_code
