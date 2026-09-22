"""astream_events → SSE 事件翻译器。

图由 langchain 的 `create_agent` 构建(见 agent/graph.py),实测节点结构:
主模型节点名是 "model",另有 "tools" / "<中间件名>.before_model|after_model" 等节点。
只翻译主模型的 token 增量(见 MAIN_MODEL_NODE),中间件节点的事件一律跳过。

A4 增强:tool 事件补充 started_at / duration_ms / exit_code / ok 字段。
"""
import json
import time
from collections.abc import AsyncIterator

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.agent.tool_display import ToolArgsStreamer, build_tool_payload

# 主 agent 的模型节点(实测确认);本项目禁用 task 子代理,不会出现子图模型事件
MAIN_MODEL_NODE = "model"

# 事件里的 args/result 展示截断
EVENT_TRUNCATE = 500

# 工具 start → end 配对计时(按事件名+run_id)
_tool_timers: dict[str, float] = {}


def _truncate(obj) -> str:
    """对象转展示字符串;ToolMessage 等不可序列化对象退化为 str()。"""
    if isinstance(obj, str):
        s = obj
    else:
        try:
            s = json.dumps(obj, ensure_ascii=False)
        except (TypeError, ValueError):
            s = str(obj)
    return s if len(s) <= EVENT_TRUNCATE else s[:EVENT_TRUNCATE] + "…"


def sse(event: str, data: dict) -> str:
    """SSE 帧:event: <name> + data: <json>。data 内 type 与 event 名一致,前端统一按 JSON 处理。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _extract_exit_code(result_str: str) -> int | None:
    """从工具 JSON 结果里尽力提取 exit_code(供前端展示)。"""
    try:
        data = json.loads(result_str)
        if isinstance(data, dict) and isinstance(data.get("exit_code"), (int, type(None))):
            return data.get("exit_code")
    except (ValueError, TypeError):
        pass
    return None


def _result_ok(result_str: str) -> bool:
    """从工具 JSON 结果里判断是否成功(status 字段)。"""
    try:
        data = json.loads(result_str)
        if isinstance(data, dict):
            return data.get("status") == "ok" or data.get("status") is None
    except (ValueError, TypeError):
        pass
    return True


def extract_interrupt(state) -> dict | None:
    """从 LangGraph StateSnapshot 中提取 HITL 中断请求(待人工审批的动作)。

    `HumanInTheLoopMiddleware` 中断时状态里会挂一个 HITLRequest:
    `{"action_requests": [{"name", "args", "description"?}], "review_configs": [...]}`;
    恢复执行用 `Command(resume={"decisions": [{"type": "approve" | "reject"}, ...]})`。
    没有中断时返回 None(不改变原有流程)。
    """
    for task in getattr(state, "tasks", ()) or ():
        for intr in getattr(task, "interrupts", ()) or ():
            value = getattr(intr, "value", None)
            if isinstance(value, dict) and value.get("action_requests"):
                return value
    return None


class _EventRecorder:
    """把增量事件合并成**有序的紧凑事件流**,供前端精确重建交错顺序。

    现状 `segments` 只能表达「思考块 + 工具卡」的顺序,正文是整体渲染的,
    无法还原「正文 → 思考 → 工具 → 正文」这种真正交错的过程(harness 靠原始
    chunk 日志做到这一点)。这里做等价但更紧凑的取舍:同类增量按到达顺序合并成
    一条(连续 delta 合成一条 text),遇到工具调用等边界事件时先提交缓冲,
    因此顺序完全保真,而条目数从"每 token 一条"降到"每段一条"。
    """

    def __init__(self, sink: list[dict] | None) -> None:
        self._sink = sink
        self._start = time.monotonic()
        self._kind: str | None = None
        self._buf: list[str] = []
        self._at = 0

    def _ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def text(self, kind: str, text: str) -> None:
        """记录一段增量文本(正文 delta / 思考 reasoning),同类连续到达会合并。"""
        if self._sink is None:
            return
        if self._kind != kind:
            self.flush()
            self._kind = kind
            self._at = self._ms()
        self._buf.append(text)

    def push(self, event: dict) -> None:
        """插入边界事件(工具开始/结束等),先把缓冲的文本提交掉以保持顺序。"""
        if self._sink is None:
            return
        self.flush()
        self._sink.append({"at": self._ms(), **event})

    def flush(self) -> None:
        if self._sink is None or not self._buf:
            return
        self._sink.append({"at": self._at, "kind": self._kind, "text": "".join(self._buf)})
        self._buf = []
        self._kind = None


async def translate(
    graph,
    config: dict,
    message: str,
    history: list | None = None,
    content_sink: list[str] | None = None,
    tool_sink: list[dict] | None = None,
    reasoning_sink: list[str] | None = None,
    timeline_sink: list[dict] | None = None,
    image_urls: list[str] | None = None,
    events_sink: list[dict] | None = None,
    resume: dict | None = None,
) -> AsyncIterator[str]:
    """消费 LangGraph astream_events,产出 SSE 帧(delta/reasoning/tool/error)。

    history:checkpointer 无历史时从 DB 回放的上下文消息(可选);
    content_sink:可选列表,增量文本追加进去,供调用方持久化部分产出;
    tool_sink:可选列表,收集完整工具调用记录(供持久化,含耗时/状态);
    reasoning_sink:可选列表,收集推理全文(供持久化,刷新后回放);
    timeline_sink:可选列表,按真实执行顺序记录 [reasoning 块 / tool 调用],
        使前端能交错渲染深度思考与工具卡;
    image_urls:图片附件绝对 URL 列表,以 OpenAI 多模态格式传入(视觉模型识别);
    events_sink:可选列表,收集合并后的有序事件流,供前端精确重建交错顺序;
    resume:人工审批后恢复执行时的 `Command(resume=...)` 载荷(HITLResponse);
        传入时不再追加用户消息,直接从中断点继续(harness 的审批恢复语义)。
    """
    # 推理全文与当前未提交的推理块(buffer 跨工具调用分割)
    reasoning_buffer: list[str] = []
    recorder = _EventRecorder(events_sink)
    # 工具调用参数的增量解析:把"正在生成的代码"变成可流式展示的帧
    args_streamer = ToolArgsStreamer()

    def flush_reasoning() -> None:
        if not reasoning_buffer:
            return
        if timeline_sink is not None:
            timeline_sink.append({"kind": "reasoning", "content": "".join(reasoning_buffer)})
        reasoning_buffer.clear()

    if resume is not None:
        # 审批恢复:从中断点继续,不再追加用户消息(中断处的状态已含该轮输入)
        inputs: dict | Command = Command(resume=resume)
    else:
        # 多模态:有图片时构造 OpenAI 兼容的 content 数组(text + image_url)
        if image_urls:
            multimodal: list[dict] = [{"type": "text", "text": message}]
            multimodal += [
                {"type": "image_url", "image_url": {"url": url}} for url in image_urls
            ]
            user_msg = HumanMessage(content=multimodal)
        else:
            user_msg = HumanMessage(content=message)
        messages = [*(history or []), user_msg]
        inputs = {"messages": messages}
    async for ev in graph.astream_events(inputs, config=config, version="v2"):
        kind = ev["event"]
        if kind == "on_chat_model_stream":
            meta = ev.get("metadata") or {}
            if meta.get("langgraph_node") != MAIN_MODEL_NODE:
                continue  # 只翻译主 agent 的 token 增量
            chunk = ev["data"]["chunk"]
            if chunk.content:
                text = str(chunk.content)
                if content_sink is not None:
                    content_sink.append(text)
                recorder.text("delta", text)
                yield sse("delta", {"type": "delta", "content": text})
            # deepseek-reasoner 等思考模型:增量在 additional_kwargs.reasoning_content
            reasoning = (chunk.additional_kwargs or {}).get("reasoning_content")
            if reasoning:
                text = str(reasoning)
                if reasoning_sink is not None:
                    reasoning_sink.append(text)
                reasoning_buffer.append(text)
                recorder.text("reasoning", text)
                yield sse("reasoning", {"type": "reasoning", "content": text})
            # 工具调用的参数增量:模型"正在写的那段代码"在这里,不解析出来前端全程看不到
            # 生成过程(只会在工具真正开始执行时看到一张卡片)。这些帧**不进** events_sink:
            # 内容会由 on_tool_start 的 payload 完整落库,没必要在事件流里存一份增量。
            for frame in args_streamer.feed(str(ev.get("run_id", "")), chunk.tool_call_chunks):
                yield sse("tool_args", {"type": "tool_args", **frame})
        elif kind == "on_tool_start":
            # 工具开始前,先把已产生的思考段落提交到时间线
            flush_reasoning()
            run_id = str(ev.get("run_id", ""))
            timer_key = f"{ev['name']}::{run_id}"
            _tool_timers[timer_key] = time.monotonic()
            data = {
                "type": "tool",
                "name": ev["name"],
                "status": "start",
                "args": _truncate(ev["data"]["input"]),
                "started_at": int(time.time() * 1000),
            }
            # 写类工具额外带一份完整载荷(path + 正文 / old+new):args 只有 500 字符,
            # 不够看真正写进去的代码,刷新后更是只剩片段
            payload = build_tool_payload(ev["name"], ev["data"]["input"])
            if payload:
                data["payload"] = payload
            recorder.push({"kind": "tool_start", "name": ev["name"], "args": data["args"]})
            if tool_sink is not None:
                # start 记录先入池,供 on_tool_end 配对合并(避免持久化重复 start/end)
                start_record = {
                    "name": ev["name"],
                    "status": "start",
                    "args": data["args"],
                    "started_at": data["started_at"],
                }
                if payload:
                    start_record["payload"] = payload
                tool_sink.append(start_record)
                if timeline_sink is not None:
                    timeline_sink.append({"kind": "tool", "call": tool_sink[-1]})
                if timeline_sink is not None:
                    timeline_sink.append({"kind": "tool", "call": tool_sink[-1]})
            yield sse("tool", data)
        elif kind == "on_tool_end":
            # 工具结束前,提交此期间产生的思考段落
            flush_reasoning()
            run_id = str(ev.get("run_id", ""))
            timer_key = f"{ev['name']}::{run_id}"
            started = _tool_timers.pop(timer_key, None)
            duration_ms = int((time.monotonic() - started) * 1000) if started is not None else None
            result = _truncate(ev["data"]["output"])
            data = {
                "type": "tool",
                "name": ev["name"],
                "status": "end",
                "result": result,
                "duration_ms": duration_ms,
                "exit_code": _extract_exit_code(result),
                "ok": _result_ok(result),
            }
            recorder.push(
                {
                    "kind": "tool_end",
                    "name": ev["name"],
                    "result": result,
                    "duration_ms": duration_ms,
                    "exit_code": data["exit_code"],
                    "ok": data["ok"],
                }
            )
            if tool_sink is not None:
                # 找到同名且仍在运行中的 start 记录,原地合并为 end(保留 started_at)
                start_rec = next(
                    (r for r in reversed(tool_sink) if r["name"] == ev["name"] and r["status"] == "start"),
                    None,
                )
                if start_rec is not None:
                    start_rec.update(
                        {
                            "status": "end",
                            "result": result,
                            "duration_ms": duration_ms,
                            "exit_code": data["exit_code"],
                            "ok": data["ok"],
                        }
                    )
                else:
                    tool_sink.append(
                        {
                            "name": ev["name"],
                            "status": "end",
                            "result": result,
                            "duration_ms": duration_ms,
                            "exit_code": data["exit_code"],
                            "ok": data["ok"],
                        }
                    )
                    if timeline_sink is not None:
                        timeline_sink.append({"kind": "tool", "call": tool_sink[-1]})
            yield sse("tool", data)
        elif kind == "on_tool_error":
            yield sse(
                "error",
                {"type": "error", "code": "tool_error", "message": _truncate(ev["data"]["error"])},
            )
    # 流结束:提交最后一个思考块与事件缓冲
    flush_reasoning()
    recorder.flush()
