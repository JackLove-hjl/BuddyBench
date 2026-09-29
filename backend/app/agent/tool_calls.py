"""工具调用的通用小工具:调用签名。

"同名同参"的判定在几个地方都要用(循环守卫判重复、审批缓存判是否问过、改动聚合),
以前只写在 loop_guard 里。签名规则必须处处一致 —— 否则 A 处认为"同一次调用"、
B 处认为"两次不同调用",两边行为就对不上了。
"""
from __future__ import annotations

import json
from typing import Any


def signature(name: str, args: Any) -> str:
    """调用签名:工具名 + 规范化参数。

    参数用 `sort_keys` 归一:模型偶尔会调换键的顺序,那仍然是同一次调用。
    """
    try:
        dumped = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        dumped = repr(args)
    return f"{name}::{dumped}"


def result_payload(message: Any) -> dict | None:
    """工具结果里的 JSON 载荷(我们的工具都返回 JSON 字符串);不是 JSON 返回 None。"""
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def failed_result(message: Any) -> bool:
    """这次工具调用是否失败。

    **不能只看 `ToolMessage.status`**:ToolNode 生成的消息恒为 `success`,工具自己的
    失败信息写在 JSON 里(`{"status": "error", "error": ...}`)。实测踩过 ——
    只看 status 属性的话,"更新缓存 / 统计改动"会把失败调用当成成功。
    """
    if str(getattr(message, "status", "")) == "error":
        return True
    payload = result_payload(message)
    return bool(payload and payload.get("status") == "error")
