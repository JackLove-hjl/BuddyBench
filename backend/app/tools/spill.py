"""工具结果 spill:超长输出落盘,模型只看到「头尾预览 + spill_id」,可再回读。

参考 deepseek-harness 的 spill-policy:超过阈值的结果写到 spillStore,模型看到的是
head/tail 预览 + locator,信息**不丢失**(需要细节时用 `read_spill` 分页读回),
而不是像静态截断那样把中间内容永久丢掉。

落盘位置:`<image_dir 的同级>/spills/<32位hex>.txt`(不在工作区内,因此不受
工作区路径约束影响;读取只经由下面这个做了严格校验的工具)。
"""
import json
import logging
import uuid
from pathlib import Path

from langchain_core.tools import tool

from app.core.config import get_settings
from app.services import retention

logger = logging.getLogger(__name__)

# 单条工具结果的字符阈值:超过则落盘
SPILL_THRESHOLD = 12000
# 预览保留的头部/尾部字符数
SPILL_HEAD = 2000
SPILL_TAIL = 500
# read_spill 单次返回上限
READ_SPILL_LIMIT = 8000
SPILL_DIR_NAME = "spills"

_HEX = set("0123456789abcdef")


def spill_dir() -> Path:
    """spill 目录(不存在则创建)。"""
    d = Path(get_settings().image_dir).parent / SPILL_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(text: str) -> str | None:
    """把文本落盘,返回 spill_id;失败返回 None。"""
    try:
        # 落盘前顺带清理过期产物(带节流,默认一小时最多真跑一次,见 services/retention.py):
        # spill 目录只增不减,而这里正是它唯一的写入点。
        retention.maybe_purge()
        sid = uuid.uuid4().hex
        (spill_dir() / f"{sid}.txt").write_text(text, encoding="utf-8")
        return sid
    except OSError as e:  # noqa: BLE001  落盘失败时调用方退回截断
        logger.warning("spill 落盘失败: %s", e)
        return None


def _preview(text: str, sid: str) -> str:
    head = text[:SPILL_HEAD]
    tail = text[-SPILL_TAIL:]
    omitted = len(text) - len(head) - len(tail)
    return (
        f"{head}\n\n… [中间省略 {omitted} 字符] …\n\n{tail}\n\n"
        f"[该结果共 {len(text)} 字符,完整内容已保存;"
        f'可用 read_spill 工具分页读取:spill_id="{sid}"]'
    )


def apply_value(value: str, kind: str) -> str:
    """单个字符串:超限则落盘并换成预览。"""
    if len(value) <= SPILL_THRESHOLD:
        return value
    sid = save(value)
    if sid is None:
        return value[:SPILL_THRESHOLD] + f"\n…(结果过长,共 {len(value)} 字符,已截断)"
    logger.info("工具结果(%s)超限 %d 字符,已 spill: %s", kind, len(value), sid)
    return _preview(value, sid)


def apply(payload: dict, kind: str = "tool") -> dict:
    """对结果的顶层字符串字段做 spill(保持 JSON 合法,前端仍可解析)。"""
    return {k: apply_value(v, kind) if isinstance(v, str) else v for k, v in payload.items()}


def dumps(payload: dict, kind: str = "tool") -> str:
    """工具结果统一序列化入口:超长字段落盘为「预览 + 回读提示」。"""
    return json.dumps(apply(payload, kind), ensure_ascii=False)


def _valid_id(spill_id: str) -> bool:
    """spill_id 必须是 32 位 hex —— 同时防路径穿越。"""
    return len(spill_id) == 32 and all(c in _HEX for c in spill_id.lower())


def make_read_spill_tool():
    """read_spill:分页读回被 spill 的完整结果(只读,不依赖会话权限)。"""

    @tool
    def read_spill(spill_id: str, offset: int = 0, limit: int = READ_SPILL_LIMIT) -> str:
        """分页读取被 read_spill 提示保存的完整工具结果(超大输出不会丢失)。

        当某个工具结果显示「… 中间省略 N 字符 …」并给出 spill_id 时,
        用它读回被省略的完整内容。offset 从 0 开始,limit 为本次返回的字符数上限。
        """
        if not _valid_id(spill_id):
            return json.dumps(
                {"status": "error", "error": "spill_id 非法(应为 32 位十六进制)"},
                ensure_ascii=False,
            )
        path = spill_dir() / f"{spill_id.lower()}.txt"
        if not path.is_file():
            return json.dumps(
                {"status": "error", "error": f"spill 不存在或已过期:{spill_id}"},
                ensure_ascii=False,
            )
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:  # noqa: BLE001
            return json.dumps({"status": "error", "error": f"读取失败:{e}"}, ensure_ascii=False)
        try:
            start = max(0, int(offset))
            size = max(1, min(int(limit), READ_SPILL_LIMIT))
        except (TypeError, ValueError):
            start, size = 0, READ_SPILL_LIMIT
        chunk = text[start : start + size]
        end = start + len(chunk)
        return json.dumps(
            {
                "status": "ok",
                "spill_id": spill_id.lower(),
                "offset": start,
                "total": len(text),
                "eof": end >= len(text),
                "content": chunk,
            },
            ensure_ascii=False,
        )

    return read_spill
