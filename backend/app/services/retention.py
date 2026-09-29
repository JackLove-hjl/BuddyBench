"""静态产物保留策略:图片 / spill / 上传 按「最长时间 + 总量上限」清理。

为什么需要:这些目录都是"用一次留一份"且**没有任何清理**——
每次工具输出过长落一个 spill 文件、每个 computer use 步骤存一张截图、每次上传建一个目录。
实测在一个本地开发环境里 `static/` 下已经积累到几十份、数 MB;spill 的报错文案里
甚至写着"不存在**或已过期**",但过期从来没被实现过。放任下去就是磁盘只涨不落,
而且这些产物还会被误当成源码提交进版本库。

两条规则(先按时间,再按总量):
  1. 修改时间早于 `asset_retention_days` 天的文件删除;
  2. 仍然超过 `asset_max_total_mb` 时,按修改时间从最旧开始删,直到降回上限以内。

安全约束(宁可少删,不可删错):
  - 只处理**显式给定**的目录,且只删文件、不跟符号链接;
  - 删完顺手清掉空目录(上传目录是按会话建的,清空后会留一堆空壳);
  - 任何异常只记日志、绝不向外抛:清理失败不该让一次工具调用失败。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_DAYS = 7
DEFAULT_MAX_TOTAL_MB = 256
# 两次清理的最小间隔:写入路径上会顺带调用(见 tools/spill.py),不能每次都全目录扫一遍
MIN_INTERVAL_SECONDS = 3600

_last_purge_at: float = 0.0


@dataclass(frozen=True)
class PurgeReport:
    """一次清理的结果(用于日志与测试断言)。"""

    deleted_files: int = 0
    deleted_bytes: int = 0
    kept_files: int = 0
    kept_bytes: int = 0

    def summary(self) -> str:
        return (
            f"删除 {self.deleted_files} 个文件({self.deleted_bytes / 1024:.0f} KB),"
            f"保留 {self.kept_files} 个({self.kept_bytes / 1024 / 1024:.1f} MB)"
        )


def asset_dirs() -> tuple[Path, ...]:
    """需要清理的目录:图片目录、spills、uploads(不存在的会在清理时跳过)。

    目录约定与应用其余部分一致(见 main.py 的静态挂载):图片目录的**兄弟目录**就是
    spills 与 uploads。`spill` 是延迟导入的 —— 它反过来要调用本模块(落盘后顺带清理),
    模块级互相导入会成环。
    """
    from app.tools import spill

    image_dir = Path(get_settings().image_dir)
    static_dir = image_dir.parent
    return (image_dir, static_dir / spill.SPILL_DIR_NAME, static_dir / "uploads")


def _collect(dirs: tuple[Path, ...]) -> list[tuple[Path, float, int]]:
    """收集 (文件, 修改时间, 大小);只收普通文件,符号链接跳过。"""
    found: list[tuple[Path, float, int]] = []
    for root in dirs:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
            except OSError:
                continue
            found.append((path, stat.st_mtime, stat.st_size))
    return found


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError as e:  # 文件被占用/权限不足:记下来,下次再试
        logger.debug("删除静态产物失败:%s(%s)", path, e)
        return False


def _prune_empty_dirs(dirs: tuple[Path, ...]) -> None:
    """清掉清空后剩下的空目录(根目录本身保留)。"""
    for root in dirs:
        if not root.is_dir():
            continue
        children = [p for p in root.rglob("*") if p.is_dir()]
        for path in sorted(children, key=lambda p: len(p.parts), reverse=True):
            try:
                path.rmdir()  # 只删空目录,非空会抛 OSError
            except OSError:
                pass


def purge(
    *,
    dirs: tuple[Path, ...] | None = None,
    max_age_days: float | None = None,
    max_total_mb: float | None = None,
    now: float | None = None,
) -> PurgeReport:
    """执行一次清理并返回结果(参数默认取配置,可显式传入以便测试)。"""
    settings = get_settings()
    targets = asset_dirs() if dirs is None else tuple(Path(d) for d in dirs)
    age_days = settings.asset_retention_days if max_age_days is None else max_age_days
    total_mb = settings.asset_max_total_mb if max_total_mb is None else max_total_mb
    now = time.time() if now is None else now

    entries = _collect(targets)
    deleted_files = 0
    deleted_bytes = 0

    # 1) 按时间:先淘汰超过保留期的
    survivors: list[tuple[Path, float, int]] = []
    if age_days > 0:
        deadline = now - age_days * 86400
        for path, mtime, size in entries:
            if mtime < deadline and _unlink(path):
                deleted_files += 1
                deleted_bytes += size
            else:
                survivors.append((path, mtime, size))
    else:
        survivors = list(entries)

    # 2) 按总量:仍超上限时,从最旧的开始删
    total_bytes = sum(size for _, _, size in survivors)
    limit_bytes = int(total_mb * 1024 * 1024)
    if limit_bytes > 0 and total_bytes > limit_bytes:
        kept: list[tuple[Path, float, int]] = []
        for entry in sorted(survivors, key=lambda item: item[1]):  # 最旧优先
            path, _mtime, size = entry
            if total_bytes > limit_bytes and _unlink(path):
                deleted_files += 1
                deleted_bytes += size
                total_bytes -= size
            else:
                kept.append(entry)  # 降回上限以内、或删除失败保留:都留在 survivors 里
        survivors = kept

    if deleted_files:
        _prune_empty_dirs(targets)
    return PurgeReport(
        deleted_files=deleted_files,
        deleted_bytes=deleted_bytes,
        kept_files=len(survivors),
        kept_bytes=total_bytes,
    )


def maybe_purge(*, force: bool = False) -> PurgeReport | None:
    """带节流的清理(写入路径上顺带调用)。

    返回 None 表示"这次没到清理时间/清理出错",调用方无需关心 —— 清理是尽力而为。
    """
    global _last_purge_at
    started = time.monotonic()
    if not force and started - _last_purge_at < MIN_INTERVAL_SECONDS:
        return None
    _last_purge_at = started
    try:
        report = purge()
    except Exception as e:  # noqa: BLE001  清理失败不能影响工具调用
        logger.warning("静态产物清理失败:%s", e)
        return None
    if report.deleted_files:
        logger.info("静态产物清理:%s", report.summary())
    return report
