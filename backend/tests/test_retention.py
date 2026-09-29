"""静态产物保留策略的测试(图片 / spill / 上传 的清理)。

这些目录以前**只增不减**:几十份 spill、几十张截图会一直堆着,而错误文案里
还写着"已过期"。这里把规则钉死,并保证三条安全约束成立:
只动给定目录、只删文件、清理失败不影响调用方。

运行:python tests/test_retention.py(或 pytest tests/)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from _harness import run

from app.services import retention

DAY = 86400


def _make_file(path: Path, *, age_days: float, size: int, now: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    ts = now - age_days * DAY
    os.utime(path, (ts, ts))
    return path


def _tmp_root() -> tuple[Path, float]:
    import tempfile

    return Path(tempfile.mkdtemp(prefix="retention-test-")), time.time()


def test_old_files_are_deleted_and_recent_kept() -> None:
    root, now = _tmp_root()
    old = _make_file(root / "spills" / "old.txt", age_days=10, size=100, now=now)
    fresh = _make_file(root / "spills" / "fresh.txt", age_days=1, size=100, now=now)

    report = retention.purge(dirs=(root / "spills",), max_age_days=7, max_total_mb=0, now=now)

    assert not old.exists(), "超过保留期的文件应被删除"
    assert fresh.exists(), "保留期内的文件必须留着"
    assert (report.deleted_files, report.kept_files) == (1, 1), report


def test_total_size_limit_removes_oldest_first() -> None:
    root, now = _tmp_root()
    mb = 1024 * 1024
    newest = _make_file(root / "images" / "a.png", age_days=1, size=mb, now=now)
    middle = _make_file(root / "images" / "b.png", age_days=2, size=mb, now=now)
    oldest = _make_file(root / "images" / "c.png", age_days=3, size=mb, now=now)

    # 关掉时间规则(0 = 该规则关闭),只看总量上限:3MB → 2MB
    report = retention.purge(dirs=(root / "images",), max_age_days=0, max_total_mb=2, now=now)

    assert not oldest.exists(), "应先删最旧的"
    assert newest.exists() and middle.exists(), "较新的文件要留着"
    assert report.kept_bytes <= 2 * mb, report


def test_files_outside_given_dirs_are_untouched() -> None:
    root, now = _tmp_root()
    inside = _make_file(root / "spills" / "old.txt", age_days=30, size=10, now=now)
    outside = _make_file(root / "important" / "keep.txt", age_days=30, size=10, now=now)

    retention.purge(dirs=(root / "spills",), max_age_days=7, max_total_mb=0, now=now)

    assert not inside.exists()
    assert outside.exists(), "没被指定的目录绝不能动"


def test_empty_dirs_are_removed_but_root_is_kept() -> None:
    root, now = _tmp_root()
    nested = _make_file(root / "uploads" / "conv-1" / "old.png", age_days=30, size=10, now=now)
    assert nested.exists()

    retention.purge(dirs=(root / "uploads",), max_age_days=7, max_total_mb=0, now=now)

    assert (root / "uploads").is_dir(), "根目录要保留(静态挂载依赖它存在)"
    assert not (root / "uploads" / "conv-1").exists(), "清空后的会话目录应被移除"


def test_missing_dirs_and_zero_age_are_safe() -> None:
    root, now = _tmp_root()
    fresh = _make_file(root / "spills" / "new.txt", age_days=1, size=10, now=now)

    report = retention.purge(
        dirs=(root / "does-not-exist", root / "spills"), max_age_days=7, max_total_mb=0, now=now
    )

    assert fresh.exists()
    assert report.deleted_files == 0, report


def test_purged_spill_reports_readable_error() -> None:
    """spill 被清理后 read_spill 要给可读错误(它的文案本来就写着"不存在或已过期")。"""
    from app.tools.spill import make_read_spill_tool

    payload = json.loads(make_read_spill_tool().invoke({"spill_id": "0" * 32}))
    assert payload["status"] == "error", payload
    assert "不存在" in payload["error"] or "过期" in payload["error"], payload


def test_asset_dirs_cover_images_spills_uploads() -> None:
    names = {path.name for path in retention.asset_dirs()}
    assert names == {"images", "spills", "uploads"}, names


if __name__ == "__main__":
    raise SystemExit(run(globals()))
