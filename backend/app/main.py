"""FastAPI 应用装配:lifespan(engine / checkpointer 初始化)、路由、静态资源、CORS。

注意:Windows 本地开发须经 run.py 启动(psycopg3 异步要求 SelectorEventLoop)。
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.db.base import engine
from app.db.checkpointer import close_checkpointer, setup_checkpointer

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await setup_checkpointer()  # LangGraph 记忆(psycopg3 独立连接)
    # 静态产物(图片 / spill / 上传)只增不减:启动时按保留策略清一次。
    # 运行期间由 spill 落盘顺带触发(带节流),见 services/retention.py。
    from app.services import retention

    report = retention.purge()
    if report.deleted_files:
        logger.info("启动清理静态产物:%s", report.summary())
    yield
    # 关掉所有终端会话:否则热重载/退出后 shell 进程会留在后台
    from app.services.terminal import registry as terminal_registry

    terminal_registry.close_all()
    await close_checkpointer()
    await engine.dispose()


app = FastAPI(title="LLM Chat Backend", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 生成的图片(M3 挂载;目录不存在时 StaticFiles 会报错,先建目录)
import os

os.makedirs(settings.image_dir, exist_ok=True)
app.mount("/images", StaticFiles(directory=settings.image_dir), name="images")

# 用户上传的图片/文件(A4 挂载)
from app.api.uploads import ensure_upload_dirs  # noqa: E402

ensure_upload_dirs()
app.mount(
    "/uploads",
    StaticFiles(directory=os.path.join(os.path.dirname(settings.image_dir), "uploads")),
    name="uploads",
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# 路由注册(M1/M2/M3 追加)
from app.api import (  # noqa: E402
    auth,
    chat,
    conversations,
    feedback,
    models,
    providers,
    terminal,
    uploads,
    workspaces,
)

app.include_router(auth.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(feedback.router, prefix="/api")
app.include_router(providers.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(workspaces.router, prefix="/api")
app.include_router(terminal.router, prefix="/api")
