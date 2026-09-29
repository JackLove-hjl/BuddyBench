"""上下文片段(Context Fragment)的测试。

三件事必须成立,否则类型化就是白做:

1. **自识别**:自己注入的消息能被认出来(渲染 → 识别);
2. **向后兼容**:历史里已是旧文案的片段仍能认出(codex 的 legacy fragment 做法)——
   老会话的 checkpointer 里存的就是旧文案,不认就等于这些判定全部静默失效;
3. **不误判**:真实工具结果/用户原话不能被当成注入片段(否则守卫会静默失效)。

再往下与真实调用点(循环守卫、截图消息识别)做集成断言 —— 只测常量对不上实现。
运行:python tests/test_fragments.py
"""
from __future__ import annotations

import json

from _harness import run

from langchain_core.messages import HumanMessage, ToolMessage

from app.agent import fragments
from app.agent.loop_guard import _is_guard_result
from app.agent.middleware import ComputerUseMiddleware

SCREEN_MARKER = "[当前屏幕截图]"


def test_every_kind_renders_and_is_detected() -> None:
    for item in fragments.KINDS:
        text = fragments.render(item.kind, "正文")
        assert fragments.contains(text, item.kind), item.kind
        assert item.kind in fragments.scan(text), item.kind
        assert fragments.is_fragment(text), item.kind


def test_legacy_text_is_still_recognized() -> None:
    """旧文案(历史里已经存下的)必须仍能识别。"""
    legacy_cases = {
        "computer.screen": f"{SCREEN_MARKER}(/images/a.jpg)这是当前屏幕;坐标请按最近一次 computer 结果里的 coordinate_space 给。",
        "computer.rejected": "本轮已拒绝电脑操作;本轮不会再执行任何屏幕操作。",
        "tool.missing_result": "(该工具调用未执行或未返回结果,已跳过)",
        "loop_guard.repeat": json.dumps(
            {"status": "error", "reason": "loop_guard", "error": "已跳过本次 read_file 调用:…"},
            ensure_ascii=False,
        ),
        "context.summary": "[先前对话摘要]\n前文讲了什么…",
    }
    for kind, text in legacy_cases.items():
        assert fragments.contains(text, kind), f"{kind} 的旧文案不再被识别:{text[:40]}"


def test_real_tool_output_is_not_a_fragment() -> None:
    """真实工具结果不能被误判成注入片段。

    `loop_guard` 曾经是裸词标记:一旦用 grep 在本仓库里搜 loop_guard,
    返回的真实结果就会被当成"守卫自己写的",守卫随之下次静默失效。
    """
    real_outputs = (
        json.dumps({"status": "ok", "matches": [{"file": "app/agent/loop_guard.py", "line": 1}]}),
        "def _is_guard_result(text: str) -> bool:  # loop_guard 的判定",
        "用户: loop_guard 是怎么判定的?",
        json.dumps({"status": "ok", "content": "1 | import json"}),
    )
    for text in real_outputs:
        assert not fragments.is_fragment(text), f"误判为注入片段:{text[:50]}"
        assert not _is_guard_result(text), f"误判为守卫结果:{text[:50]}"


def test_tool_error_shape_and_detection() -> None:
    """片段式工具错误:JSON 里有 status/reason,正文里有标记,两条识别路径都通。"""
    raw = fragments.tool_error("loop_guard.repeat", "已跳过本次调用")
    payload = json.loads(raw)
    assert payload["status"] == "error"
    assert payload["reason"] == "loop_guard.repeat"
    assert payload["error"].startswith("[ctx:loop_guard.repeat] ")
    assert _is_guard_result(raw)
    assert _is_guard_result(fragments.tool_error("loop_guard.stalled", "画面没变化"))


def test_loop_guard_result_is_excluded_but_real_result_is_not() -> None:
    """守卫结果不参与"结果是否相同"的比较;真实结果参与(否则守卫再也拦不住)。"""
    assert _is_guard_result(fragments.tool_error("loop_guard.repeat", "x"))
    assert not _is_guard_result(json.dumps({"status": "ok", "content": "同样的真实结果"}))


def test_screen_message_detection_handles_new_and_legacy() -> None:
    """中间件对"截图消息"的识别:新标记与旧文案都要认(否则会重复注入/不删旧图)。"""
    fresh = HumanMessage(
        content=[
            {
                "type": "text",
                "text": fragments.render("computer.screen", "(/images/x.jpg)这是当前屏幕;"),
            },
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        ]
    )
    legacy = HumanMessage(
        content=[
            {"type": "text", "text": f"{SCREEN_MARKER}(/images/x.jpg)这是当前屏幕;"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        ]
    )
    plain = HumanMessage(content="普通用户消息")
    assert ComputerUseMiddleware._is_screen_message(fresh)
    assert ComputerUseMiddleware._is_screen_message(legacy)
    assert not ComputerUseMiddleware._is_screen_message(plain)


def test_rejected_tool_result_is_detected() -> None:
    """被拒绝的电脑操作:新标记与旧文案都要认(否则"拒绝后短路"会失效)。"""
    fresh = ToolMessage(
        content=fragments.render("computer.rejected", "本轮已拒绝电脑操作;本轮不会再执行任何屏幕操作。"),
        tool_call_id="1",
        name="computer",
        status="error",
    )
    legacy = ToolMessage(
        content="本轮已拒绝电脑操作;本轮不会再执行任何屏幕操作。",
        tool_call_id="1",
        name="computer",
        status="error",
    )
    ok = ToolMessage(
        content=json.dumps({"status": "ok", "action": "click", "screen_changed": True}),
        tool_call_id="1",
        name="computer",
    )
    for message in (fresh, legacy):
        assert fragments.contains(str(message.content), "computer.rejected")
    assert not fragments.contains(str(ok.content), "computer.rejected")


if __name__ == "__main__":
    raise SystemExit(run(globals()))
