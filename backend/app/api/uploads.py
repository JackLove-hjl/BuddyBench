"""文件上传 API:图片 / 本地文件。

- 图片:存 uploads/images/ 下,返回 url(前端预览 + 消息引用)
- 本地文件:存 uploads/files/ 下,返回 url(读取文本内容注入 chat)
- 均按用户子目录隔离(user_id/uuid)防串扰
"""
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.base import get_session
from app.db.models import User

router = APIRouter(prefix="/uploads", tags=["uploads"])

# 图片扩展名(白名单);文件放行常见文本/文档类型,其余拒绝
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
FILE_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".log",
    ".html", ".css", ".xml", ".yaml", ".yml", ".sh", ".sql", ".ini",
    ".cfg", ".toml", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".rb",
}

MAX_SIZE = 20 * 1024 * 1024  # 20MB


def _safe_ext(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return ext if len(ext) <= 10 else ""


@router.post("", status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    ext = _safe_ext(file.filename or "")
    is_image = ext in IMAGE_EXTS
    if not is_image and ext not in FILE_EXTS:
        raise HTTPException(
            status_code=400,
            detail={"code": "unsupported_type", "message": f"不支持的文件类型 .{ext or 'unknown'}"},
        )
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise HTTPException(status_code=400, detail={"code": "file_too_large", "message": "文件超过 20MB 限制"})
    if len(data) == 0:
        raise HTTPException(status_code=400, detail={"code": "empty_file", "message": "文件为空"})

    settings = get_settings()
    sub = "images" if is_image else "files"
    user_dir = Path(settings.image_dir).parent / "uploads" / sub / str(user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest = user_dir / stored_name
    dest.write_bytes(data)

    url = f"/uploads/{sub}/{user.id}/{stored_name}"
    return {"url": url, "filename": file.filename or stored_name, "kind": "image" if is_image else "file", "size": len(data)}


def ensure_upload_dirs() -> None:
    """启动时确保 uploads 目录存在(StaticFiles 挂载前置)。"""
    settings = get_settings()
    base = Path(settings.image_dir).parent / "uploads"
    (base / "images").mkdir(parents=True, exist_ok=True)
    (base / "files").mkdir(parents=True, exist_ok=True)
