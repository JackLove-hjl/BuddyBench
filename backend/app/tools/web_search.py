"""联网工具:web_search(Tavily 搜索) / web_fetch(网页抓取)。

- web_search 调用 Tavily Search API,需在 .env 配置 TAVILY_API_KEY(https://tavily.com)
- web_fetch 直接抓取网页并把 HTML 转换为纯文本(无需 key)

只读性质,不依赖会话权限;由"联网"开关控制是否装配到 Agent。
"""
import json
import logging
import urllib.parse
from html.parser import HTMLParser

import httpx
from langchain_core.tools import tool

from app.tools import spill

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_SEARCH_DEPTH = "basic"  # basic / advanced / fast / ultra-fast(advanced 计 2 credits)
TAVILY_TOPIC = "general"  # general / news / finance
TAVILY_TIMEOUT = 20.0

DEFAULT_MAX_RESULTS = 6
MAX_RESULTS_LIMIT = 10  # Tavily 上限 20,这里收紧避免上下文爆炸
MAX_BODY_CHARS = 200_000  # web_fetch 正文保留上限(超出部分由 spill 兜底,不静默丢弃)
MAX_BYTES = 1024 * 1024  # 抓取内容上限

MISSING_KEY_ERROR = (
    "联网搜索未配置:请先在 backend/.env 中设置 TAVILY_API_KEY(https://tavily.com 申请),"
    "然后重启后端。"
)


def _json(status: str, **kw) -> str:
    # 超长正文(web_fetch)自动 spill,避免静态截断丢内容
    return spill.dumps({"status": status, **kw}, kind="web")


class _TextExtractor(HTMLParser):
    """HTML → 纯文本(保留段落换行,丢弃 script/style)。"""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: D102
        if tag in ("script", "style", "noscript", "template"):
            self._skip += 1
        if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "pre"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:  # noqa: D102
        if tag in ("script", "style", "noscript", "template"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data: str) -> None:  # noqa: D102
        if self._skip == 0:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        # 压缩连续空白/空行
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        return "\n".join(lines)


async def _tavily_search(api_key: str, query: str, max_results: int) -> list[dict]:
    """调用 Tavily Search API,归一化为统一的结果列表。"""
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": TAVILY_SEARCH_DEPTH,
        "topic": TAVILY_TOPIC,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    async with httpx.AsyncClient(timeout=TAVILY_TIMEOUT) as client:
        resp = await client.post(
            TAVILY_SEARCH_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
    data = resp.json()

    items: list[dict] = []
    for r in data.get("results") or []:
        if not isinstance(r, dict):
            continue
        items.append(
            {
                "title": (r.get("title") or "").strip(),
                "url": r.get("url") or "",
                "snippet": (r.get("content") or "").strip(),
                "score": r.get("score"),
                "published_date": r.get("published_date"),
            }
        )
    return items


def _fetch(url: str, timeout: float = 15.0) -> tuple[str, dict]:
    """抓取 URL 返回 (最终地址, 响应头)。非 HTTP(S) 直接拒绝。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"仅支持 http/https 地址: {url}")
    resp = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    resp.raise_for_status()
    return str(resp.url), dict(resp.headers)


def make_web_tools(api_key: str | None = None) -> list:
    """返回联网工具列表(由联网开关决定是否装配)。

    api_key 为空时仍装配工具,调用 web_search 会返回可读的"未配置"提示,
    避免 agent 静默地失去联网能力。
    """
    key = (api_key or "").strip()

    @tool
    async def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str:
        """搜索互联网并返回结果列表(标题 / 链接 / 摘要)。

        当问题涉及实时信息、最新新闻、不在你的知识范围内或需要验证事实时使用。
        只读操作,返回最多 max_results 条结果(默认 6,上限 10)。
        """
        if not key:
            return _json("error", error=MISSING_KEY_ERROR)
        try:
            max_results = max(1, min(int(max_results), MAX_RESULTS_LIMIT))
            items = await _tavily_search(key, query, max_results)
            if not items:
                return _json("error", error=f"未搜索到「{query}」的相关结果")
            return _json("ok", query=query, count=len(items), results=items)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in (401, 403):
                logger.warning("tavily auth failed: %s", e)
                return _json("error", error="Tavily API Key 无效或已过期,请检查 backend/.env 中的 TAVILY_API_KEY")
            if status == 429:
                return _json("error", error="Tavily 请求过于频繁,请稍后重试")
            if status in (432, 433):
                return _json("error", error="Tavily 配额已用尽,请检查账户套餐或用量")
            logger.warning("web_search failed: %s", e)
            return _json("error", error=f"搜索失败:HTTP {status}")
        except Exception as e:  # noqa: BLE001
            logger.warning("web_search failed: %s", e)
            return _json("error", error=f"搜索失败:{e}")

    @tool
    async def web_fetch(url: str) -> str:
        """抓取指定网页并将正文转换为纯文本(最多 {MAX_BODY_CHARS} 字符)。

        用于阅读 web_search 结果中的具体页面,或访问已知网址。
        """
        try:
            final_url, headers = _fetch(url)
            content_type = headers.get("content-type", "")
            async with httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                resp = await client.get(final_url)
                resp.raise_for_status()
                body = resp.text
            if body is None or len(body.encode("utf-8", errors="replace")) > MAX_BYTES:
                return _json("error", error="页面内容过大")
            parser = _TextExtractor()
            parser.feed(body)
            text = parser.text()
            if len(text) > MAX_BODY_CHARS:
                text = text[:MAX_BODY_CHARS] + "\n…(内容截断)"
            if not text.strip():
                return _json("error", error="无法提取正文(可能是 JS 渲染页面)", content_type=content_type)
            return _json("ok", url=final_url, content_type=content_type, content=text)
        except Exception as e:  # noqa: BLE001
            logger.warning("web_fetch failed: %s", e)
            return _json("error", error=f"抓取失败:{e}")

    return [web_search, web_fetch]
