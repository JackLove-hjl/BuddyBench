"""FastAPI 应用装配:lifespan(engine / checkpointer 初始化)、路由、静态资源、CORS。

注意:Windows 本地开发须经 run.py 启动(psycopg3 异步要求 SelectorEventLoop)。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.db.base import engine
from app.db.checkpointer import close_checkpointer, setup_checkpointer


@asynccontextmanager
async def lifespan(app: FastAPI):
    await setup_checkpointer()  # LangGraph 记忆(psycopg3 独立连接)
    yield
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
from app.api import auth, chat, conversations, models, providers, uploads, workspaces  # noqa: E402

app.include_router(auth.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(providers.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(workspaces.router, prefix="/api")
