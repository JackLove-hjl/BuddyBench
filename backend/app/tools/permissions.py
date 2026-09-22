"""Agent 权限模型:权限常量、门控工厂与工作区路径解析(防穿越)。

三级权限:
- read_only            只读:可读工作区/项目文件,禁止写文件与执行命令
- workspace_writable   工作区可写:工作区内读写文件 + 执行命令,不可越出工作区
- full_access          全部:不受限读写与执行命令(前端需二次风险确认)
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PERMISSION_READ_ONLY = "read_only"
PERMISSION_WORKSPACE_WRITABLE = "workspace_writable"
PERMISSION_FULL_ACCESS = "full_access"

PERMISSIONS = frozenset({PERMISSION_READ_ONLY, PERMISSION_WORKSPACE_WRITABLE, PERMISSION_FULL_ACCESS})


class PermissionDenied(Exception):
    """权限不足 / 路径越界,工具应返回给 agent 的可读错误。"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def validate_permission(permission: str) -> str:
    if permission not in PERMISSIONS:
        raise ValueError(f"unknown permission: {permission}")
    return permission


def permission_gate(permission: str, *, need_write: bool = False, need_shell: bool = False) -> None:
    """工具调用时强制校验权限。

    - 需要写文件(need_write):read_only 拒绝
    - 需要执行 shell(need_shell):read_only 拒绝;workspace_writable 允许
    - full_access 全放行
    """
    if permission == PERMISSION_FULL_ACCESS:
        return
    if permission == PERMISSION_READ_ONLY and (need_write or need_shell):
        raise PermissionDenied(
            "当前会话为「只读」权限,仅可读取工作区/项目文件,禁止写文件或执行命令。"
            "请切换为「工作区可写」或「全部权限」后再试。"
        )
    if permission == PERMISSION_WORKSPACE_WRITABLE and need_shell:
        # 工作区可写:允许在工作区内的命令执行,见 resolve_workspace_path 边界
        return


def resolve_workspace_path(path: str, workspace_root: str | None) -> Path:
    """把 agent 提供的路径解析为工作区内的绝对路径;`..` 穿越与越界一律拒绝。

    workspace_root 为 None 时拒绝任何文件操作(未选择工作区)。
    返回 path.resolve() 后仍位于 workspace_root 内部的 Path。
    """
    if not workspace_root:
        raise PermissionDenied("当前会话未选择工作区,文件操作不可用。请先在工作区入口选择本地文件夹。")
    root = Path(workspace_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise PermissionDenied(f"工作区路径不存在或不是目录:{root}")
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        p = root / p
    resolved = p.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PermissionDenied(
            f"路径越界:目标 {resolved} 不在工作区 {root} 内。工作区可写权限禁止访问工作区之外的文件。"
        )
    return resolved
