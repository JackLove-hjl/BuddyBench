"""新增中间件与工具的测试(P1-4 / P1-5 / P1-6 / P2-7)。

覆盖四件事,每件都对应一个"以前做不到、现在要做到"的能力:

1. **上下文预算**:模型问一次就能拿到 已用/剩余/阈值/建议,且只应答自己的工具;
2. **审批缓存**:本轮内已成功跑过的同名同参调用不再重复问;**失败与被拒绝的不入缓存**;
3. **本轮改动清单**:只统计成功的写操作、相对路径、去重,并在内容变化时替换上一次注入;
4. **等待环境**:端口在听/文件存在 → 就绪;没人听 → 超时并给出可读原因;HTTP 4xx 也算活着。

运行:python tests/test_agent_guards.py(或 pytest tests/)
"""
from __future__ import annotations

import json
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from _harness import BACKEND_ROOT, run

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from app.agent import fragments
from app.agent.approval_cache import ApprovalCacheMiddleware, approved_signatures
from app.agent.budget import ContextBudgetMiddleware
from app.agent.turn_changes import TurnChangesMiddleware, changed_files
from app.agent.tool_calls import signature
from app.tools import env_wait
from app.tools.context_budget import CONTEXT_TOOL_NAME, get_context_remaining
from app.tools.env_wait import wait_for_environment


class FakeRequest:
    """ToolCallRequest 的最小替身(我们的中间件只用 getattr 读这几个属性)。"""

    def __init__(self, name: str, args: dict | None = None, *, call_id: str = "call-1", messages=()):
        self.tool_call = {"name": name, "args": args or {}, "id": call_id}
        self.state = {"messages": list(messages)}
        self.tool = None
        self.runtime = None


def _success(call_id: str, name: str, args: dict, payload: dict) -> list:
    return [
        AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}]),
        ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id=call_id, name=name),
    ]


# ---------- P1-4 上下文预算 ----------


def test_context_budget_reports_numbers_and_advice() -> None:
    middleware = ContextBudgetMiddleware(
        context_window=10_000, trigger_fraction=0.7, system_prompt="系统提示"
    )
    report = middleware.report([HumanMessage(content="x" * 100)])
    assert report["model_context_window"] == 10_000
    assert report["auto_compact_threshold_tokens"] == 7_000
    assert report["used_tokens"] > 0
    assert report["remaining_tokens"] == 10_000 - report["used_tokens"]
    assert report["advice"]  # 一定给一句可执行建议

    # 已经很满:建议里必须提到压缩阈值(让模型知道下一步会发生什么)
    tight = middleware.report([HumanMessage(content="x" * 40_000)])
    assert "压缩" in tight["advice"]


def test_context_budget_unknown_window_is_honest() -> None:
    report = ContextBudgetMiddleware(context_window=0).report([])
    assert report["model_context_window"] == 0
    assert report["used_percent"] is None
    assert "无法判断" in report["advice"]


def test_context_budget_answers_only_its_own_tool() -> None:
    middleware = ContextBudgetMiddleware(context_window=1000)
    assert middleware._answer(FakeRequest("read_file", {"path": "a"})) is None

    answer = middleware._answer(FakeRequest(CONTEXT_TOOL_NAME, call_id="c9"))
    assert answer is not None
    assert answer.tool_call_id == "c9"
    payload = json.loads(str(answer.content))
    assert payload["status"] == "ok" and payload["model_context_window"] == 1000


def test_context_tool_is_registered_for_every_tier() -> None:
    """上下文预算工具在所有权限档位都装配(它是只读的)。"""
    from _harness import PERMISSIONS, tool_context
    from app.tools.registry import registry

    for permission in PERMISSIONS:
        names = {tool.name for tool in registry.build(tool_context(permission))}
        assert CONTEXT_TOOL_NAME in names, permission
    # 工具本体在被绕过中间件执行时,也必须给可读说明而不是崩
    payload = json.loads(get_context_remaining.invoke({}))
    assert payload["status"] == "error" and payload["error"]


# ---------- P1-5 审批缓存 ----------


def test_approved_signatures_only_counts_successful_calls() -> None:
    messages = _success("c1", "run_shell_command", {"command": "git status"}, {"status": "ok"})
    messages += _success("c2", "run_shell_command", {"command": "git diff"}, {"status": "error"})
    approved = approved_signatures([HumanMessage(content="开始"), *messages])
    assert signature("run_shell_command", {"command": "git status"}) in approved
    assert signature("run_shell_command", {"command": "git diff"}) not in approved


def test_approval_cache_hit_requires_same_args_in_current_turn() -> None:
    middleware = ApprovalCacheMiddleware(tools=frozenset({"run_shell_command"}))
    messages = [HumanMessage(content="开始"), *_success("c1", "run_shell_command", {"command": "git status"}, {"status": "ok"})]

    hit = FakeRequest("run_shell_command", {"command": "git status"}, messages=messages)
    miss = FakeRequest("run_shell_command", {"command": "git clean -fd"}, messages=messages)
    assert middleware._hit(hit) is True
    assert middleware._hit(miss) is False, "换了命令必须重新问"


def test_approval_cache_ignores_tools_without_approval() -> None:
    middleware = ApprovalCacheMiddleware(tools=frozenset({"run_shell_command"}))
    messages = [HumanMessage(content="开始"), *_success("c1", "read_file", {"path": "a"}, {"status": "ok"})]
    assert middleware._hit(FakeRequest("read_file", {"path": "a"}, messages=messages)) is False


def test_approval_cache_does_not_reuse_previous_turn() -> None:
    """上一轮批准过的命令,这一轮必须重新问(缓存的生命周期是"本轮")。"""
    middleware = ApprovalCacheMiddleware(tools=frozenset({"run_shell_command"}))
    old_turn = _success("c1", "run_shell_command", {"command": "git status"}, {"status": "ok"})
    messages = [HumanMessage(content="上一轮"), *old_turn, HumanMessage(content="新的一轮")]
    assert middleware._hit(FakeRequest("run_shell_command", {"command": "git status"}, messages=messages)) is False


# ---------- P2-7 本轮改动清单 ----------


def test_changed_files_relative_dedup_and_skips_failures() -> None:
    root = BACKEND_ROOT
    abs_file = str((root / "app" / "main.py").resolve())
    other = str((root / "app" / "agent" / "graph.py").resolve())
    messages = [
        HumanMessage(content="改一下"),
        *_success("c1", "write_file", {"path": abs_file}, {"status": "ok", "path": abs_file}),
        *_success("c2", "edit_file", {"path": abs_file}, {"status": "ok", "path": abs_file}),
        *_success("c3", "edit_file", {"path": other}, {"status": "ok", "path": other}),
        *_success("c4", "write_file", {"path": "/tmp/x"}, {"status": "error", "error": "拒绝"}),
    ]
    assert changed_files(messages, str(root)) == ("app/main.py", "app/agent/graph.py")


def test_turn_changes_injects_then_replaces() -> None:
    root = str(BACKEND_ROOT)
    abs_file = str((BACKEND_ROOT / "app" / "main.py").resolve())
    middleware = TurnChangesMiddleware(workspace_root=root)
    base = [HumanMessage(content="改一下"), *_success("c1", "write_file", {"path": abs_file}, {"status": "ok", "path": abs_file})]

    update = middleware._update({"messages": base})
    assert update is not None
    injected = update["messages"][0]
    assert isinstance(injected, HumanMessage)
    assert fragments.contains(str(injected.content), "context.turn_changes")
    assert "app/main.py" in str(injected.content)

    # 同一份清单:不重复注入
    injected.id = "frag-1"
    assert middleware._update({"messages": [*base, injected]}) is None

    # 本轮没有改动:把上一次的清单撤掉(否则它会冒充本轮改动)
    removal = middleware._update({"messages": [HumanMessage(content="新的问题"), injected]})
    assert removal is not None and isinstance(removal["messages"][0], RemoveMessage)


def test_turn_messages_ignores_injected_fragments() -> None:
    """注入片段不算轮次边界:轮次应从上一条真正的用户消息开始。"""
    fragment = HumanMessage(content=fragments.render("context.turn_changes", "本轮改了 a.py"))
    messages = [HumanMessage(content="第一条"), HumanMessage(content="第二条"), fragment]
    turn = fragments.turn_messages(messages)
    assert turn[0].content == "第二条", "轮次边界必须是真正的用户消息"
    assert fragment in turn, "片段本身仍属于本轮(动作计数 / 截图识别要用到)"


def test_failed_result_reads_json_not_message_status() -> None:
    """失败判定必须看结果 JSON:ToolNode 生成的 ToolMessage.status 恒为 success。

    这是实测踩过的坑 —— 只看 status 属性时,"失败不缓存 / 失败不算改动"两条规则会静默失效。
    """
    from app.agent.tool_calls import failed_result

    failed = ToolMessage(
        content=json.dumps({"status": "error", "error": "权限不足"}), tool_call_id="c1", name="write_file"
    )
    ok = ToolMessage(
        content=json.dumps({"status": "ok", "path": "a.py"}), tool_call_id="c2", name="write_file"
    )
    assert failed_result(failed) and not failed_result(ok)
    # 失败结果不计入审批缓存
    messages = [HumanMessage(content="x"), AIMessage(content="", tool_calls=[{"name": "write_file", "args": {}, "id": "c1"}]), failed]
    assert approved_signatures(messages) == set()
    # 失败结果也不计入本轮改动清单
    assert changed_files(messages, None) == ()


# ---------- P1-6 等待环境 ----------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_wait_for_environment_port_ready() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        payload = json.loads(
            wait_for_environment.invoke({"target": f"127.0.0.1:{port}", "timeout_seconds": 5})
        )
    finally:
        server.close()
    assert payload["status"] == "ok", payload
    assert payload["attempts"] >= 1


def test_wait_for_environment_file_ready() -> None:
    path = Path(tempfile.mkdtemp()) / "built.js"
    path.write_text("x", encoding="utf-8")
    payload = json.loads(wait_for_environment.invoke({"target": str(path)}))
    assert payload["status"] == "ok", payload


def test_wait_for_environment_timeout_is_readable() -> None:
    port = _free_port()  # 绑定后立即关闭:这个端口没人听
    payload = json.loads(
        wait_for_environment.invoke(
            {"target": f"127.0.0.1:{port}", "timeout_seconds": 0.5, "interval_seconds": 0.2}
        )
    )
    assert payload["status"] == "error", payload
    assert "未就绪" in payload["error"]
    assert payload["waited_seconds"] >= 0.5


def test_probe_treats_http_4xx_as_alive() -> None:
    """dev server 起来了但路径 404:对"服务可用"来说也算活着。"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802  http.server 的接口名
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args: object) -> None:  # 静音测试输出
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ready, reason = env_wait.probe(f"http://127.0.0.1:{server.server_port}/nope")
    finally:
        server.shutdown()
        server.server_close()
    assert ready, reason


# ---------- 压缩与片段联动 ----------


def test_tool_result_display_strips_fragment_tags() -> None:
    """展示层剥掉 [ctx:*] 标记,但模型侧读到的仍是带标记的原文。"""
    from app.agent.bridge import _truncate

    message = ToolMessage(
        content=fragments.tool_error("loop_guard.repeat", "已跳过本次调用"),
        tool_call_id="c1",
        name="read_file",
    )
    shown = _truncate(message)
    assert "[ctx:" not in shown
    assert "已跳过本次调用" in shown
    assert "[ctx:" in str(message.content), "模型侧必须保留类型标记(片段识别依赖它)"


def test_summary_input_drops_machine_tags_and_folds_screens() -> None:
    from app.services.compaction import messages_to_text

    screen = HumanMessage(
        content=[
            {"type": "text", "text": fragments.render("computer.screen", "(/images/a.jpg)这是当前屏幕;")},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        ]
    )
    text = messages_to_text([HumanMessage(content="帮我看看界面"), screen])
    assert "[ctx:" not in text
    assert "(屏幕截图,已省略)" in text
    assert "帮我看看界面" in text


if __name__ == "__main__":
    raise SystemExit(run(globals()))
