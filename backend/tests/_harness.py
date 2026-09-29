"""测试用最小基础设施。

这个仓库的后端此前没有测试目录,也没有装 pytest。为了让断言**现在就能跑**
(而不是"等以后装了 pytest 再说"),测试统一写成 pytest 风格(函数名 `test_` 开头),
同时不依赖 pytest:

    python tests/test_tool_specs.py     # 直接跑:用下面的 run() 收集执行
    pytest tests/                       # 装上 pytest 后自动采集,不用改一行

导入本模块即把 backend 根目录加入 sys.path(测试从 tests/ 目录运行时也能 import app.*)。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.tools.permissions import (  # noqa: E402
    PERMISSION_FULL_ACCESS,
    PERMISSION_READ_ONLY,
    PERMISSION_WORKSPACE_WRITABLE,
)
from app.tools.registry import ToolContext  # noqa: E402

PERMISSIONS = (PERMISSION_READ_ONLY, PERMISSION_WORKSPACE_WRITABLE, PERMISSION_FULL_ACCESS)

# 组合遍历(权限档位 × 计划模式 × computer 开关):工具装配条件最容易漂移的就是这几个维度
CONTEXT_MATRIX = [
    (permission, plan_mode, computer)
    for permission in PERMISSIONS
    for plan_mode in (False, True)
    for computer in (False, True)
]


def tool_context(permission: str, *, plan_mode: bool = False, computer: bool = False) -> ToolContext:
    """构造一个不碰真实环境的 ToolContext(图片目录用临时目录)。"""
    return ToolContext(
        permission=permission,
        workspace_root=str(BACKEND_ROOT),
        image_dir=Path(tempfile.mkdtemp(prefix="backend-test-")),
        plan_mode=plan_mode,
        computer_use_enabled=computer,
        tavily_api_key="test-key",
    )


def run(namespace: dict) -> int:
    """执行 namespace 里所有 `test_` 开头的函数,返回失败数。"""
    tests = [
        (name, value)
        for name, value in sorted(namespace.items())
        if name.startswith("test_") and callable(value)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as e:  # noqa: BLE001  测试跑手:任何异常都算失败
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed
