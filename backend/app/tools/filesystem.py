"""文件系统工具:read_file / write_file / edit_file / list_dir / glob / grep(含路径穿越防护)。

权限:
- read_file / list_dir / glob / grep:任何权限均可(read_only 允许读)
- write_file / edit_file:read_only 拒绝;workspace_writable 仅限工作区内;full 不受限

输出体积恒定:所有工具都受下面的 *_LIMIT / MAX_* 常量约束,单次调用不会撑爆上下文。
"""
import json
import logging
import os
import re
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from langchain_core.tools import tool

from app.tools import spill
from app.tools.permissions import PermissionDenied, permission_gate, resolve_workspace_path

logger = logging.getLogger(__name__)

# 返回给 agent 的文本截断
READ_LIMIT = 20000
LIST_LIMIT = 200
# 可安全读取的二进制以外文本文件大小上限(避免拖垮上下文)
MAX_READ_BYTES = 512 * 1024
# grep / glob 的扫描上限(控制单次调用的耗时与输出体积)
GREP_MAX_RESULTS = 50  # 默认返回条数
GREP_HARD_LIMIT = 200  # 参数可调上限
GREP_MAX_FILES = 2000  # 最多扫描的文件数
GREP_LINE_LIMIT = 300  # 单行匹配文本截断
GLOB_MAX_RESULTS = 300  # 最多返回的路径数
GLOB_MAX_FILES = 5000  # 最多遍历的文件数
MAX_SCAN_BYTES = 1024 * 1024  # 单个被扫描文件大小上限
# 遍历时跳过的重型目录(避免扫 .venv / node_modules 拖慢速度并污染结果)
SKIP_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", ".idea", ".vscode",
        "node_modules", ".venv", "venv", "__pycache__",
        "dist", "build", ".next", "target",
        ".mypy_cache", ".pytest_cache", ".ruff_cache",
    }
)


def _json(status: str, **kw) -> str:
    # 超长字段(如 read_file 正文)自动 spill:换成头尾预览 + spill_id,
    # 信息不再被静态截断丢掉,模型可用 read_spill 分页读回
    return spill.dumps({"status": status, **kw}, kind="filesystem")


def _rel_path(p: Path, workspace_root: str | None) -> str:
    """返回相对工作区的 posix 路径(便于模型直接复用)。"""
    if workspace_root:
        try:
            return p.resolve().relative_to(Path(workspace_root).expanduser().resolve()).as_posix()
        except ValueError:
            pass
    return p.as_posix()


def _read_scan_text(p: Path) -> str | None:
    """读取用于扫描的文本;二进制或超过 MAX_SCAN_BYTES 返回 None。"""
    try:
        if p.stat().st_size > MAX_SCAN_BYTES:
            return None
        raw = p.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:  # 含 NUL 视为二进制
        return None
    return raw.decode("utf-8", errors="replace")


def _walk_files(root: Path, limit: int):
    """遍历 root 下文件,跳过 SKIP_DIRS 与符号链接,最多产出 limit 个。"""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if count >= limit:
                return
            count += 1
            yield Path(dirpath) / name


def make_filesystem_tools(permission: str, workspace_root: str | None):
    """返回文件工具三元组(工厂,按会话权限/工作区重建)。"""

    @tool
    def read_file(path: str) -> str:
        """读取工作区内的文本文件并返回内容(带行号)。

        优先使用本工具而非 shell 的 cat;只读权限可用。文件超过 512KB 只返回开头与结尾。
        """
        try:
            p = resolve_workspace_path(path, workspace_root)
            if not p.is_file():
                return _json("error", error=f"文件不存在:{p}")
            if p.stat().st_size > MAX_READ_BYTES:
                return _json("error", error=f"文件过大({p.stat().st_size} 字节 > 512KB),请改用 shell 分段查看")
            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            numbered = "\n".join(f"{i + 1:4d} | {ln}" for i, ln in enumerate(lines))
            if len(numbered) > READ_LIMIT:
                numbered = numbered[:READ_LIMIT] + "\n…(截断)"
            return _json("ok", path=str(p), content=numbered)
        except PermissionDenied as e:
            return _json("error", error=e.message)
        except Exception as e:  # noqa: BLE001
            return _json("error", error=f"读取失败:{e}")

    @tool
    def write_file(path: str, content: str) -> str:
        """创建工作区内的新文件或完全替换已有文件内容。目录会自动创建。

        写文件前建议先用 read_file 了解现状。工作区可写权限下仅允许操作工作区内路径。
        """
        try:
            permission_gate(permission, need_write=True)
            p = resolve_workspace_path(path, workspace_root)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return _json("ok", path=str(p), bytes=len(content.encode("utf-8")))
        except PermissionDenied as e:
            return _json("error", error=e.message)
        except Exception as e:  # noqa: BLE001
            return _json("error", error=f"写入失败:{e}")

    @tool
    def list_dir(path: str) -> str:
        """列出工作区内目录的内容(条目名 + 类型)。默认当前目录,可传子路径。

        只读权限可用。用于了解项目结构。
        """
        try:
            p = resolve_workspace_path(path or ".", workspace_root)
            if not p.is_dir():
                return _json("error", error=f"目录不存在:{p}")
            entries = []
            for child in sorted(p.iterdir()):
                try:
                    kind = "dir" if child.is_dir() else "file"
                    entries.append({"name": child.name, "type": kind})
                except OSError:
                    continue
                if len(entries) >= LIST_LIMIT:
                    entries.append({"name": "…", "type": "truncated"})
                    break
            return _json("ok", path=str(p), entries=entries)
        except PermissionDenied as e:
            return _json("error", error=e.message)
        except Exception as e:  # noqa: BLE001
            return _json("error", error=f"列出失败:{e}")

    @tool
    def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """精确替换文件中的一段文本(推荐用于修改已有文件)。

        old_string 必须与文件内容完全一致(含缩进与换行),并具备足够上下文以唯一定位。
        匹配到多处时会报错,请补充上下文或设置 replace_all=True。
        比 write_file 重写整个文件更安全、更省 token。只读权限下返回权限错误。
        """
        try:
            permission_gate(permission, need_write=True)
            p = resolve_workspace_path(path, workspace_root)
            if not p.is_file():
                return _json("error", error=f"文件不存在:{p}")
            if p.stat().st_size > MAX_READ_BYTES:
                return _json(
                    "error",
                    error=f"文件过大({p.stat().st_size} 字节 > 512KB),请改用 shell 或分段处理",
                )
            if old_string == new_string:
                return _json("error", error="old_string 与 new_string 相同,无需修改")
            if not old_string:
                return _json("error", error="old_string 不能为空;如需整体覆盖请使用 write_file")
            text = p.read_text(encoding="utf-8", errors="replace")
            count = text.count(old_string)
            if count == 0:
                return _json(
                    "error",
                    error="未找到 old_string —— 请先用 read_file 确认原文(缩进与换行需完全一致)",
                )
            if count > 1 and not replace_all:
                return _json(
                    "error",
                    error=(
                        f"old_string 匹配到 {count} 处,无法确定目标:"
                        "请补充上下文使其唯一,或设置 replace_all=True 全部替换"
                    ),
                )
            if replace_all:
                new_text = text.replace(old_string, new_string)
            else:
                new_text = text.replace(old_string, new_string, 1)
            p.write_text(new_text, encoding="utf-8")
            return _json(
                "ok",
                path=str(p),
                replaced=count if replace_all else 1,
                bytes=len(new_text.encode("utf-8")),
            )
        except PermissionDenied as e:
            return _json("error", error=e.message)
        except Exception as e:  # noqa: BLE001
            return _json("error", error=f"编辑失败:{e}")

    @tool
    def glob(pattern: str, path: str = ".") -> str:
        """按通配符查找工作区内的文件路径(如 "*.py"、"src/*.ts"、"**/test_*.py")。

        只读权限可用。返回相对工作区的 posix 路径列表。
        用于"知道文件名但不知道位置"的场景;已知内容片段请用 grep。
        匹配 Python fnmatch / pathlib 语义(`*` 可跨目录)。默认跳过 .git/.venv/node_modules 等。
        """
        try:
            root = resolve_workspace_path(path or ".", workspace_root)
            if not root.exists():
                return _json("error", error=f"路径不存在:{root}")
            base = root if root.is_dir() else root.parent
            matched: list[str] = []
            truncated = False
            for f in _walk_files(base, GLOB_MAX_FILES):
                rel = _rel_path(f, workspace_root)
                if not (fnmatch(rel, pattern) or PurePosixPath(rel).match(pattern)):
                    continue
                matched.append(rel)
                if len(matched) >= GLOB_MAX_RESULTS:
                    truncated = True
                    break
            if not matched:
                return _json("ok", pattern=pattern, count=0, files=[], note="未匹配到任何文件")
            return _json(
                "ok", pattern=pattern, count=len(matched), files=matched, truncated=truncated
            )
        except PermissionDenied as e:
            return _json("error", error=e.message)
        except Exception as e:  # noqa: BLE001
            return _json("error", error=f"查找失败:{e}")

    @tool
    def grep(
        pattern: str,
        path: str = ".",
        file_glob: str | None = None,
        max_results: int = GREP_MAX_RESULTS,
    ) -> str:
        """在工作区文件内容中按正则搜索,返回 文件 / 行号 / 匹配行。

        只读权限可用。用于定位函数定义、调用点、日志文案等,比逐个 read_file 快得多。
        file_glob 可按文件名收窄范围(如 "*.py")。默认跳过 .git/.venv/node_modules 等目录。
        """
        try:
            root = resolve_workspace_path(path or ".", workspace_root)
            if not root.exists():
                return _json("error", error=f"路径不存在:{root}")
            try:
                rx = re.compile(pattern)
            except re.error as e:
                return _json("error", error=f"正则表达式无效:{e}")
            limit = max(1, min(int(max_results), GREP_HARD_LIMIT))
            targets = [root] if root.is_file() else _walk_files(root, GREP_MAX_FILES)
            matches: list[dict] = []
            scanned = 0
            truncated = False
            for f in targets:
                if file_glob and not fnmatch(f.name, file_glob):
                    continue
                text = _read_scan_text(f)
                if text is None:
                    continue
                scanned += 1
                rel = _rel_path(f, workspace_root)
                for lineno, line in enumerate(text.splitlines(), 1):
                    if not rx.search(line):
                        continue
                    matches.append({"file": rel, "line": lineno, "text": line.strip()[:GREP_LINE_LIMIT]})
                    if len(matches) >= limit:
                        truncated = True
                        break
                if truncated:
                    break
            if not matches:
                return _json(
                    "ok", pattern=pattern, count=0, matches=[], scanned=scanned, note="未匹配到任何内容"
                )
            return _json(
                "ok",
                pattern=pattern,
                count=len(matches),
                matches=matches,
                scanned=scanned,
                truncated=truncated,
            )
        except PermissionDenied as e:
            return _json("error", error=e.message)
        except Exception as e:  # noqa: BLE001
            return _json("error", error=f"搜索失败:{e}")

    return read_file, write_file, edit_file, list_dir, glob, grep
