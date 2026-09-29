"""交互式终端会话(Windows ConPTY / POSIX PTY)。

分工:后端只搬运字节,**不做终端模拟** —— 光标、ANSI 转义、回滚缓冲全交给前端的
xterm.js。后端因此不维护任何屏幕状态,换终端渲染器时这里不用动。

为什么不用 `POST /exec` 那种一次性管道:真终端需要持久会话(cd/环境变量要留在
shell 里)、需要 stdin 流(否则没有回显与交互提示)、需要窗口尺寸(否则换行错乱)。

Windows 走 ConPTY(pywinpty),POSIX 走同一套 API(pywinpty 在非 Windows 上是
ptyprocess 的封装),所以只有一份会话实现。
"""

import asyncio
import logging
import os
import shutil
import sys
import threading
import uuid
from dataclasses import dataclass

from winpty import PtyProcess

logger = logging.getLogger(__name__)

# 单次从 PTY 读取的字节数:太小会把多字节字符切碎在两次读取之间
READ_SIZE = 8192
# 每个用户同时最多开几个终端(避免误开一堆 shell 把机器拖垮)
MAX_SESSIONS_PER_USER = 6
# 单条输入上限(一次粘贴的量级)
MAX_INPUT_CHARS = 64_000
# 终端尺寸夹取范围
MIN_COLS, MAX_COLS = 20, 500
MIN_ROWS, MAX_ROWS = 4, 200

# PowerShell(5.1 与 7 通用)切到 UTF-8。
# 特意写成不带空格的一行:argv 会被拼成命令行,避免引号处理带来的坑。
#
# 三件事分别解决一层编码:
#   1) Console.OutputEncoding / InputEncoding —— 进出控制台的字节按 UTF-8 走;
#   2) chcp 65001 —— 底层的代码页(原生程序如 git/type 也受影响);
#   3) Get-Content 的默认编码 —— **最容易踩的一层**:Windows PowerShell 5.1 的
#      Get-Content(cat / type / gc 都是它的别名)默认按系统 ANSI 代码页(简中即 GBK)
#      解码文件,而这里的文件基本都是 UTF-8,于是 `cat xxx.txt` 中文全是乱码。
#      PowerShell 7 默认就是 UTF-8,5.1 不是,所以显式补上(只改读取,不动写入,
#      免得会话里写文件被悄悄换成别的编码)。
PS_UTF8 = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "[Console]::InputEncoding=[Text.Encoding]::UTF8;"
    "$PSDefaultParameterValues['Get-Content:Encoding']='utf8';"
    "$PSDefaultParameterValues['Import-Csv:Encoding']='utf8';"
    # 不能用 `chcp 65001>$null`(实测会打印 "Parameter format not correct - 65001>"),
    # 走管道吞掉提示行才稳
    "chcp 65001 | Out-Null"
)
CMD_UTF8 = "chcp 65001>nul"


@dataclass(frozen=True)
class ShellSpec:
    """一个可选 shell:前端「+ 新建终端」据此列出菜单。"""

    key: str
    label: str
    argv: tuple[str, ...]


def _which(*candidates: str) -> str | None:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def available_shells() -> list[ShellSpec]:
    """本机可用的 shell 列表(按推荐顺序)。"""
    if sys.platform == "win32":
        shells: list[ShellSpec] = []
        cmd = os.environ.get("COMSPEC") or _which("cmd.exe") or "cmd.exe"
        # 进 UTF-8 代码页:否则中文输入按 GBK 进 shell、输出也会乱码(实测过)
        shells.append(ShellSpec("cmd", "命令提示符 (cmd)", (cmd, "/K", CMD_UTF8)))
        pwsh = _which("pwsh.exe", "pwsh")
        if pwsh:
            shells.append(ShellSpec("pwsh", "PowerShell 7 (pwsh)", (pwsh, "-NoLogo", "-NoExit", "-Command", PS_UTF8)))
        powershell = _which("powershell.exe", "powershell")
        if powershell:
            shells.append(
                ShellSpec("powershell", "Windows PowerShell", (powershell, "-NoLogo", "-NoExit", "-Command", PS_UTF8))
            )
        return shells
    shell_path = os.environ.get("SHELL") or _which("bash", "sh") or "/bin/sh"
    return [ShellSpec("bash", os.path.basename(shell_path), (shell_path, "-i"))]


def find_shell(key: str) -> ShellSpec:
    """按 key 找 shell;找不到(或没传)时退回第一个可用的。"""
    shells = available_shells()
    wanted = key.strip().lower()
    for spec in shells:
        if spec.key == wanted:
            return spec
    return shells[0]


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


class TerminalSession:
    """一个终端会话 = 一个 PTY 进程 + 一个读取线程 + 一个 asyncio 队列。

    PTY 的 read 是阻塞调用,所以放在独立线程里读,读到就丢进队列;WebSocket 那边
    只 await 队列 —— 事件循环不会被阻塞,终端输出也不会因为别处忙而卡住。
    """

    def __init__(
        self,
        *,
        spec: ShellSpec,
        cwd: str,
        env: dict[str, str],
        user_id: str,
        rows: int,
        cols: int,
    ) -> None:
        self.id = uuid.uuid4().hex
        self.spec = spec
        self.user_id = user_id
        self.cwd = cwd
        self.rows = clamp(rows, MIN_ROWS, MAX_ROWS)
        self.cols = clamp(cols, MIN_COLS, MAX_COLS)
        self._env = env
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        self._process: PtyProcess | None = None
        self._thread: threading.Thread | None = None

    # --- 生命周期 ---------------------------------------------------------
    def start(self) -> None:
        """启动 shell(阻塞;调用方用 to_thread 包起来)。"""
        self._process = PtyProcess.spawn(
            list(self.spec.argv),
            cwd=self.cwd,
            env=self._env,
            dimensions=(self.rows, self.cols),
        )
        self._thread = threading.Thread(target=self._pump, name=f"pty-{self.id[:8]}", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """结束会话(连子进程一起杀)。"""
        process = self._process
        if process is None:
            return
        try:
            if process.isalive():
                process.terminate(force=True)
        except Exception:  # noqa: BLE001  进程可能已自行退出
            logger.debug("terminal %s terminate failed", self.id, exc_info=True)
        try:
            process.close()
        except Exception:  # noqa: BLE001
            pass

    # --- 数据通路 ---------------------------------------------------------
    def _post(self, payload: bytes | None) -> None:
        """在工作线程里投递数据(经 call_soon_threadsafe,所以这里已经是主线程)。"""
        self._queue.put_nowait(payload)

    def _pump(self) -> None:
        """读取线程:PTY → 队列。收到 EOF 时投一个 None 作为结束哨兵。"""
        process = self._process
        if process is None:
            return
        try:
            while True:
                try:
                    data = process.read(READ_SIZE)
                except EOFError:
                    break
                if not data:
                    break
                payload = data.encode("utf-8", "replace") if isinstance(data, str) else data
                self._loop.call_soon_threadsafe(self._post, payload)
        except Exception:  # noqa: BLE001  shell 被杀时会抛,属正常收尾
            logger.debug("terminal %s reader stopped", self.id, exc_info=True)
        finally:
            self._loop.call_soon_threadsafe(self._post, None)

    async def read(self) -> bytes | None:
        """取一段输出;None 表示 shell 已结束。"""
        return await self._queue.get()

    def write(self, data: str) -> None:
        if self._process is not None and data:
            self._process.write(data)

    def resize(self, rows: int, cols: int) -> None:
        if self._process is None:
            return
        self.rows = clamp(rows, MIN_ROWS, MAX_ROWS)
        self.cols = clamp(cols, MIN_COLS, MAX_COLS)
        try:
            self._process.setwinsize(self.rows, self.cols)
        except Exception:  # noqa: BLE001  进程刚退出时会抛
            logger.debug("terminal %s resize failed", self.id, exc_info=True)

    @property
    def alive(self) -> bool:
        return bool(self._process is not None and self._process.isalive())

    @property
    def exit_code(self) -> int | None:
        if self._process is None:
            return None
        status = self._process.exitstatus
        return status if isinstance(status, int) else None


class TerminalRegistry:
    """会话登记表:限制每人会话数,后台退出时统一清场(避免留下孤儿 shell)。"""

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()

    def count_for(self, user_id: str) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.user_id == user_id)

    def add(self, session: TerminalSession) -> None:
        with self._lock:
            self._sessions[session.id] = session

    def remove(self, session: TerminalSession) -> None:
        with self._lock:
            self._sessions.pop(session.id, None)

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()


registry = TerminalRegistry()
