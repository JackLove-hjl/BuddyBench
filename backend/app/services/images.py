"""本机图片 → base64 data url 的统一转换。

为什么需要它:第三方 OpenAI 兼容网关**无法回拉 localhost 图片**,凡是需要让模型"看见"
的本机图片(用户上传的图片、computer use 的屏幕截图),都必须内联成 data url。

两个使用方:
- `app/agent/middleware.py` 的 ComputerUseMiddleware:注入屏幕截图给模型看;
- `app/services/chat_service.py`:回放历史时把 `/images/...` 一并转换(否则刷新后模型拉不到图)。
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 支持的图片后缀 → mime(与 image_dir 里实际落盘的格式一致)
_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

IMAGES_URL_PREFIX = "/images/"

MAX_INLINE_BYTES = 4 * 1024 * 1024  # 超过 4MB 不内联(避免把上下文撑爆)


def image_url_to_data_url(url: str, image_dir: Path) -> str | None:
    """把 `/images/<name>` 转成 data url;不是本机图片或读取失败时返回 None。"""
    if not url or not str(url).startswith(IMAGES_URL_PREFIX):
        return None
    name = str(url)[len(IMAGES_URL_PREFIX) :].split("?", 1)[0].split("#", 1)[0]
    if not name or "/" in name or "\\" in name or ".." in name:
        return None  # 只接受 image_dir 下的平铺文件名,防目录穿越
    path = Path(image_dir) / name
    try:
        raw = path.read_bytes()
    except OSError:
        logger.warning("图片不存在或不可读,跳过内联:%s", path)
        return None
    if len(raw) > MAX_INLINE_BYTES:
        logger.warning("图片过大(%d 字节),跳过内联:%s", len(raw), path)
        return None
    mime = _MIME.get(path.suffix.lower(), "image/png")
    return f"data:{mime};base64," + base64.b64encode(raw).decode()
