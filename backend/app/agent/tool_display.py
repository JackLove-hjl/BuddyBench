"""工具调用的展示层:参数流式解析 + 完整内容持久化。

解决两个具体问题(都源自"args 只是一坨被截断的 JSON"):

1. **生成期间一片空白**(`ToolArgsStreamer`)
   模型写文件时,那段代码在流式协议里是**工具调用的参数增量**(`chunk.tool_call_chunks`),
   不是正文 delta。桥接层原先只翻译正文与思考,于是"决定写文件 → 工具开始执行"之间
   没有任何事件,大文件看起来像卡死。这里把参数增量解析成可供前端边生成边显示的文本。

2. **工具卡里看不到真正写进去的内容**(`build_tool_payload`)
   `args` 在桥接层被截到 500 字符,落库的 `meta.tool_records` 也一并被截,
   导致刷新后依然看不到完整代码。这里为写类工具额外抽一份 payload(path + 正文 /
   old_string + new_string),上限放宽到 TOOL_PAYLOAD_LIMIT。
"""
from __future__ import annotations

import re

# 每个工具的展示字段(单一来源:流式展示与持久化 payload 共用):
# 第一个非 path 字段是流式正文,edit_file 是"旧串 → 新串"两段。
STREAM_FIELDS: dict[str, tuple[str, ...]] = {
    "write_file": ("path", "content"),
    "edit_file": ("path", "old_string", "new_string"),
    "run_python_code": ("code",),
    "run_shell_command": ("command",),
}

TOOL_PAYLOAD_LIMIT = 64 * 1024  # 单个字段持久化上限(字符);超出截断并标记 truncated
PATH_LIMIT = 500

# 键名到值之间的形状:`"path" : "..."`
_KEY_VALUE_RE = re.compile(r'\s*:\s*"')

_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    '"': '"',
    "\\": "\\",
    "/": "/",
}


def payload_fields(name: str) -> tuple[str, ...]:
    """payload 里要保留的字段(流式字段去掉 path)。"""
    return tuple(f for f in STREAM_FIELDS.get(name, ()) if f != "path")


def build_tool_payload(name: str, args) -> dict | None:
    """为写类工具抽一份展示载荷;不关心的工具返回 None。"""
    fields = payload_fields(name)
    if not fields or not isinstance(args, dict):
        return None
    payload: dict = {}
    path = args.get("path")
    if isinstance(path, str) and path:
        payload["path"] = path[:PATH_LIMIT]
    for field in fields:
        value = args.get(field)
        if isinstance(value, str) and value:
            if len(value) > TOOL_PAYLOAD_LIMIT:
                payload[field] = value[:TOOL_PAYLOAD_LIMIT]
                payload["truncated"] = True
            else:
                payload[field] = value
    return payload or None


class _FieldStreamer:
    """从不断增长的(可能不完整的)JSON 文本里,增量提取某个字符串字段的已解码内容。

    JSON 还没闭合、转义序列只到一半都没关系 —— 半个转义留到下一块再解。
    逐字符状态机 + 单调游标,只处理新到达的部分,大文件不会退化成 O(n²)。
    """

    def __init__(self, field: str) -> None:
        self._needle = f'"{field}"'
        self._scan = 0  # 找键名的扫描起点
        self._cursor = 0  # 读值时的游标
        self._state = "seek"  # seek → value → done
        self.produced = 0  # 已产出的解码文本长度

    def feed(self, raw: str) -> str:
        """返回本次新解码出来的文本(没有新增则返回空串)。"""
        if self._state == "done":
            return ""
        if self._state == "seek":
            pos = raw.find(self._needle, self._scan)
            if pos == -1:
                # 键名可能被切在两块之间:回退一个键长,下块再找
                self._scan = max(0, len(raw) - len(self._needle))
                return ""
            matched = _KEY_VALUE_RE.match(raw, pos + len(self._needle))
            if matched is None:
                self._scan = pos  # 冒号/引号还没到,下块重试
                return ""
            self._cursor = matched.end()
            self._state = "value"

        out: list[str] = []
        i = self._cursor
        end = len(raw)
        while i < end:
            ch = raw[i]
            if ch == "\\":
                if i + 1 >= end:
                    break  # 半个转义,留到下一块
                esc = raw[i + 1]
                if esc == "u":
                    if i + 6 > end:
                        break  # \uXXXX 还没到齐
                    try:
                        out.append(chr(int(raw[i + 2 : i + 6], 16)))
                    except ValueError:
                        break
                    i += 6
                else:
                    out.append(_ESCAPES.get(esc, esc))
                    i += 2
            elif ch == '"':
                self._state = "done"
                i += 1
                break
            else:
                out.append(ch)
                i += 1
        self._cursor = i
        text = "".join(out)
        self.produced += len(text)
        return text


class _OneToolCall:
    """一次工具调用的参数流。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self._raw = ""
        self._streamers = {f: _FieldStreamer(f) for f in STREAM_FIELDS.get(name, ())}
        self._announced = False

    def feed(self, chunk: str) -> list[dict]:
        # 不关心的工具(只读类、评审类)不产生任何展示帧:参数很短,没有"生成过程"可看,
        # 凭空多一张卡反而噪音
        if not self._streamers:
            return []
        frames: list[dict] = []
        # 先把"开始生成参数"的帧发出去,前端据此立刻画出卡片(否则大文件生成期间没有任何反馈)
        if not self._announced:
            self._announced = True
            frames.append(self._frame(None, "", 0))
        self._raw += chunk
        for field, streamer in self._streamers.items():
            delta = streamer.feed(self._raw)
            if delta:
                frames.append(self._frame(field, delta, streamer.produced))
        return frames

    def _frame(self, field: str | None, delta: str, size: int) -> dict:
        return {"name": self.name, "field": field, "delta": delta, "size": size}


class ToolArgsStreamer:
    """把流式的工具调用参数增量,翻译成"正在生成参数"的展示帧。

    按 `run_id + index` 区分:同一轮回复里模型可能并行发起多个工具调用,
    而不同轮次又会从 index 0 重新开始计数。
    """

    def __init__(self) -> None:
        self._calls: dict[str, _OneToolCall] = {}

    def feed(self, run_id: str, chunks) -> list[dict]:
        frames: list[dict] = []
        for ch in chunks or ():
            if not isinstance(ch, dict):
                continue
            index = ch.get("index")
            key = f"{run_id}:{0 if index is None else int(index)}"
            call = self._calls.get(key)
            name = ch.get("name") or ""
            if call is None:
                if not name:
                    continue  # 名字还没到,等下一块
                call = _OneToolCall(name)
                self._calls[key] = call
            frames.extend(call.feed(ch.get("args") or ""))
        return frames
