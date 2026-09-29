"""工作区管理(本机模式):目录浏览 + 直接登记本地绝对路径。

本机模式下后端跑在用户电脑上(非容器),能直接访问本地文件系统。
工作区 = 用户本地文件夹的绝对路径,后端校验存在性后直接登记,Agent 工具直接读写该目录。
浏览器安全模型禁止 JS 读取绝对路径,因此由后端提供目录浏览 API(Windows 盘符 + 目录树)。
"""
import asyncio
import ctypes
import logging
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.conversations import _get_conversation_or_404
from app.api.deps import get_current_user
from app.db.base import get_session
from app.db.models import User
from app.schemas.conversation import ConversationOut
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# 单次浏览最多列出的子目录数
BROWSE_LIMIT = 300
# 单次浏览最多扫描的目录项数(防止超大目录拖慢响应)
BROWSE_SCAN_LIMIT = 50000
# GetDriveTypeW 返回值(0/1 表示不可用)
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOTE = 4
# 网络驱动器连通性探测超时(秒):断开的映射盘会让文件系统调用阻塞到 SMB 超时
DRIVE_PROBE_TIMEOUT = 0.5
# 探测线程池(只在 Windows 上用于网络驱动器,避免阻塞请求线程)
_DRIVE_PROBE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="drive-probe")
# 跳过系统/隐藏目录,避免噪音
SKIP_DIRS = frozenset(
    {
        "$recycle.bin", "system volume information", "recovery", "perflogs",
        "node_modules", ".git", ".venv", "venv", "__pycache__", ".cache",
        ".codebuddy", ".playwright-cli", ".idea", ".vscode", "dist", ".next",
        ".nuxt", "windows", "windows.old", "program files", "program files (x86)",
        "programdata", "syswow64",
    }
)


class WorkspaceSetBody(BaseModel):
    workspace_path: str | None = Field(default=None, max_length=1000)


class ExecBody(BaseModel):
    """侧栏终端里用户敲的一条命令。"""

    command: str = Field(min_length=1, max_length=4000)
    # 工作区内相对目录(空 = 工作区根)
    cwd: str = Field(default="", max_length=1000)


def _probe_reachable(roots: list[str], timeout: float = DRIVE_PROBE_TIMEOUT) -> set[str]:
    """并发探测一组盘符里哪些真的可访问,整体等待不超过一个 timeout。

    断开的映射网络驱动器上 `os.path.exists` 会阻塞到 SMB 超时(实测 ~21 秒),
    所以放进线程池并发跑并加超时;超时即视为不可达,被放弃的线程在后台自行结束。
    """
    if not roots:
        return set()
    futures = {root: _DRIVE_PROBE_POOL.submit(os.path.exists, root) for root in roots}
    deadline = time.monotonic() + timeout
    ready: set[str] = set()
    for root, future in futures.items():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            if future.result(timeout=remaining):
                ready.add(root)
        except FuturesTimeoutError:
            break
        except Exception:  # noqa: BLE001  探测失败一律当作不可达
            continue
    return ready


def _list_roots() -> list[str]:
    """浏览起点:Windows 列出盘符,其余平台返回 /。

    必须用 Win32 API 枚举,不能逐个 os.path.exists("X:\\"):
    在「已断开的映射网络驱动器」上 exists 会一直等到 SMB 超时(实测两个断开的
    映射盘各 ~21 秒,合计 42 秒),而 GetLogicalDrives / GetDriveTypeW 是纯本地
    查询,不触碰网络设备,实测 <1ms。
    """
    if sys.platform != "win32":
        return ["/"]
    try:
        kernel32 = ctypes.windll.kernel32
        mask = kernel32.GetLogicalDrives()
        candidates: list[tuple[str, int]] = []
        for i in range(26):
            if not (mask >> i) & 1:
                continue
            root = f"{chr(ord('A') + i)}:\\"
            dtype = kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
            # 0=UNKNOWN / 1=NO_ROOT_DIR 不可用;可移动/固定/网络/光驱/内存盘都保留
            if dtype <= DRIVE_NO_ROOT_DIR:
                continue
            candidates.append((root, dtype))
        # 已断开的映射网络驱动器也在 GetLogicalDrives 里,但访问它要等 SMB 超时;
        # 并发探测一次,不可达就不展示,避免用户点进去卡住
        remote = [root for root, dtype in candidates if dtype == DRIVE_REMOTE]
        ready = _probe_reachable(remote)
        return [root for root, dtype in candidates if dtype != DRIVE_REMOTE or root in ready]
    except (AttributeError, OSError):  # pragma: no cover - 非预期平台/API 缺失
        import string

        return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]


def _browse_dir(path: str) -> dict:
    """列出指定目录的子目录(带父目录路径)。

    用 os.scandir 而不是 sorted(p.iterdir(), key=...is_dir()):后者会对**每个**
    目录项做一次 stat,大目录下明显变慢;scandir 在 Windows 上直接读 dirent 里
    的文件属性就能判断目录,省掉逐项 stat。另外父目录已 resolve,子项直接拼接即可,
    不再逐个 resolve(那又是一次文件系统调用)。
    """
    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": f"目录不存在:{p}"})
    children: list[dict] = []
    try:
        with os.scandir(p) as it:
            for scanned, entry in enumerate(it, start=1):
                if scanned > BROWSE_SCAN_LIMIT:
                    break
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                if entry.name.lower() in SKIP_DIRS:
                    continue
                children.append({"name": entry.name, "path": str(p / entry.name)})
    except PermissionError:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": f"无权访问目录:{p}"})
    except OSError as e:
        raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": f"读取目录失败:{e}"})
    # 只对筛出来的目录排序,避免排序键对全部条目做 is_dir
    children.sort(key=lambda c: c["name"].lower())
    del children[BROWSE_LIMIT:]
    parent = str(p.parent) if p.parent != p else None
    return {"current": str(p), "parent": parent, "children": children}


def _resolve_workspace(root_path: str) -> Path:
    """解析工作区根目录,不存在则 400。"""
    root = Path(root_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": f"工作区目录不存在:{root}"})
    return root


def _resolve_rel(root: Path, rel: str) -> Path:
    """把工作区内相对路径解析为绝对路径,防穿越。"""
    if rel.strip():
        p = Path(rel.strip().replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts:
            raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": f"非法路径:{rel}"})
        target = (root / p).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": "路径越界"})
        return target
    return root


def _list_files(root: Path, rel: str) -> dict:
    """列出工作区内目录/文件(@ 选择)。rel 为空=工作区根。"""
    cur = _resolve_rel(root, rel)
    if not cur.exists() or not cur.is_dir():
        raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": f"目录不存在:{cur}"})
    dirs: list[dict] = []
    files: list[dict] = []
    try:
        # 同 _browse_dir:scandir 避免逐项 stat,只在需要文件大小时才 stat
        with os.scandir(cur) as it:
            for scanned, entry in enumerate(it, start=1):
                if scanned > BROWSE_SCAN_LIMIT:
                    break
                try:
                    rel_str = Path(entry.path).relative_to(root).as_posix()
                    if entry.is_dir():
                        if entry.name.lower() not in SKIP_DIRS:
                            dirs.append({"name": entry.name, "path": rel_str, "kind": "dir"})
                    else:
                        files.append(
                            {
                                "name": entry.name,
                                "path": rel_str,
                                "kind": "file",
                                "size": entry.stat().st_size,
                            }
                        )
                except OSError:
                    continue
    except OSError as e:
        raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": f"读取失败:{e}"})
    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda f: f["name"].lower())
    cur_rel = cur.relative_to(root).as_posix() if cur != root else ""
    return {
        "current": cur_rel,
        "parent": (cur.parent.relative_to(root).as_posix() if cur.parent != root else "") if cur != root else None,
        "root": str(root),
        "dirs": dirs,
        "files": files,
    }


# --- 文件内容缓存与「非阻塞读盘」 ---------------------------------------------
# 前端每次点开文件都会发一个请求:同一文件被反复开关标签、工具卡来回查看时会重复读盘。
# 缓存用 (mtime_ns, size) 校验,Agent 改写文件后自动失效 —— 不会出现"看到旧内容"。
_CONTENT_CACHE: dict[str, tuple[int, int, str]] = {}
_CONTENT_CACHE_MAX = 32
# 单文件读取上限(与前端提示一致)
READ_MAX_BYTES = 512 * 1024


def _cached_read_text(target: Path) -> str:
    """读文本文件;mtime/大小未变则直接返回缓存内容(须在工作线程中调用)。"""
    st = target.stat()
    key = str(target)
    hit = _CONTENT_CACHE.get(key)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        # 命中后挪到末尾,保持「最久没用过的先淘汰」
        _CONTENT_CACHE.pop(key, None)
        _CONTENT_CACHE[key] = hit
        return hit[2]
    text = target.read_text(encoding="utf-8", errors="replace")
    _CONTENT_CACHE[key] = (st.st_mtime_ns, st.st_size, text)
    while len(_CONTENT_CACHE) > _CONTENT_CACHE_MAX:
        _CONTENT_CACHE.pop(next(iter(_CONTENT_CACHE)))
    return text


def _read_workspace_file_sync(root_path: str, rel: str) -> dict:
    """解析路径 → 校验 → 读内容(含缓存)。整体在工作线程里跑。

    为什么不直接在 async 端点里写:FastAPI 的 async 端点就跑在事件循环上,而
    `Path.resolve/stat/read_text` 都是阻塞调用 —— 磁盘慢、目录巨大或工作区在
    网络盘上时,会把整个后端(包括正在输出的 SSE)一起卡住,用户感受就是
    "点开文件转圈很久,而且这时别的操作也一起卡"。
    """
    root = _resolve_workspace(root_path)
    target = _resolve_rel(root, rel)
    if not target.is_file():
        raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": f"文件不存在:{rel}"})
    if target.stat().st_size > READ_MAX_BYTES:
        raise HTTPException(status_code=400, detail={"code": "file_too_large", "message": "文件超过 512KB,请让 Agent 自行读取"})
    return {"content": _cached_read_text(target), "path": target.relative_to(root).as_posix()}


def _list_files_by_root_sync(root_path: str, rel: str) -> dict:
    return _list_files(_resolve_workspace(root_path), rel)


async def _in_thread(fn, *args):
    """把阻塞的文件系统操作放进线程池,不占事件循环。"""
    return await to_thread.run_sync(fn, *args)


@router.get("/browse")
async def browse_directories(path: str = "") -> dict:
    """浏览服务器目录:path 为空返回起点(Windows 盘符 / Linux 根),否则返回子目录。"""
    if not path.strip():
        return await _in_thread(lambda: {"current": None, "parent": None, "roots": _list_roots(), "children": []})
    return await _in_thread(_browse_dir, path.strip())


@router.get("/files")
async def list_files_by_path(
    workspace_path: str,
    path: str = "",
    user: User = Depends(get_current_user),
) -> dict:
    """按工作区绝对路径列出目录/文件(@ 选择)。供新对话(尚未创建会话)使用。"""
    return await _in_thread(_list_files_by_root_sync, workspace_path, path)


@router.get("/read")
async def read_file_by_path(
    workspace_path: str,
    path: str,
    user: User = Depends(get_current_user),
) -> dict:
    """按工作区绝对路径读取文件内容(@ 引用)。供新对话(尚未创建会话)使用。"""
    return await _in_thread(_read_workspace_file_sync, workspace_path, path)


@router.get("/{conversation_id}/files")
async def list_workspace_files(
    conversation_id: uuid.UUID,
    path: str = "",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """列出会话工作区目录/文件(@ 选择)。path 为工作区内的相对路径(空=工作区根)。"""
    conv = await _get_conversation_or_404(session, conversation_id, user.id)
    if not conv.workspace_path:
        raise HTTPException(status_code=400, detail={"code": "no_workspace", "message": "当前会话未设置工作区"})
    return await _in_thread(_list_files_by_root_sync, conv.workspace_path, path)


@router.get("/{conversation_id}/read")
async def read_workspace_file(
    conversation_id: uuid.UUID,
    path: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """读取会话工作区文件内容(@ 引用时注入对话)。限文本,512KB 内。"""
    conv = await _get_conversation_or_404(session, conversation_id, user.id)
    if not conv.workspace_path:
        raise HTTPException(status_code=400, detail={"code": "no_workspace", "message": "当前会话未设置工作区"})
    return await _in_thread(_read_workspace_file_sync, conv.workspace_path, path)


# 侧栏终端:单条命令最长执行时间与输出上限(与 run_shell_command 同量级)
EXEC_TIMEOUT = 60
EXEC_OUTPUT_LIMIT = 200_000


def _decode_tail(raw: bytes | None) -> str:
    """子进程输出解码:优先 utf-8,中文 Windows 的 cmd 输出是 GBK,失败再退 replace。"""
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)[-EXEC_OUTPUT_LIMIT:]
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")[-EXEC_OUTPUT_LIMIT:]


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """超时后连子进程一起杀,避免留下后台进程。"""
    try:
        import psutil

        for child in psutil.Process(proc.pid).children(recursive=True):
            child.kill()
    except Exception:  # noqa: BLE001  进程可能已退出
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass


@router.post("/{conversation_id}/exec")
async def exec_in_workspace(
    conversation_id: uuid.UUID,
    body: ExecBody,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """在会话工作区执行一条命令(侧栏终端,用户主动发起)。

    与 `run_shell_command` 同一套底线:只读档位直接拒绝、cwd 限制在工作区内、
    环境变量剔除 KEY/TOKEN/SECRET、超时杀进程树、输出截断。
    这里不做模型侧的审批(命令是用户自己敲的),但档位门控保留 —— 否则"只读会话"
    里靠终端就能绕过权限设置。
    """
    conv = await _get_conversation_or_404(session, conversation_id, user.id)
    if conv.permission == "read_only":
        raise HTTPException(
            status_code=403,
            detail={"code": "permission_denied", "message": "当前会话是「只读」档位,不允许在工作区执行命令"},
        )
    if not conv.workspace_path:
        raise HTTPException(status_code=400, detail={"code": "no_workspace", "message": "当前会话未设置工作区"})
    root = _resolve_workspace(conv.workspace_path)
    cwd = _resolve_rel(root, body.cwd or "")
    if not cwd.is_dir():
        raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": f"目录不存在:{body.cwd}"})

    from app.tools.shell import _scrub_env  # 复用同一套环境变量剔除规则

    try:
        proc = await asyncio.create_subprocess_shell(
            body.command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_scrub_env(),
        )
    except OSError as e:
        raise HTTPException(status_code=400, detail={"code": "exec_failed", "message": f"无法启动命令:{e}"})

    timed_out = False
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=EXEC_TIMEOUT)
    except asyncio.TimeoutError:
        timed_out = True
        _kill_tree(proc)
        out, err = b"", f"命令执行超过 {EXEC_TIMEOUT} 秒,已终止".encode()

    cwd_rel = cwd.relative_to(root).as_posix() if cwd != root else ""
    return {
        "exit_code": None if timed_out else proc.returncode,
        "stdout": _decode_tail(out),
        "stderr": _decode_tail(err),
        "cwd": cwd_rel,
        "timed_out": timed_out,
    }


@router.put("/{conversation_id}", response_model=ConversationOut)
async def set_workspace(
    conversation_id: uuid.UUID,
    body: WorkspaceSetBody,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """登记会话工作区:直接引用本地绝对路径,零上传。"""
    conv = await _get_conversation_or_404(session, conversation_id, user.id)
    path = body.workspace_path
    if path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": "工作区必须是绝对路径"})
        if not p.exists() or not p.is_dir():
            raise HTTPException(status_code=400, detail={"code": "invalid_path", "message": f"工作区目录不存在:{p}"})
        conv.workspace_path = str(p.resolve())
    else:
        conv.workspace_path = None
    await session.commit()
    await session.refresh(conv)
    return conv
