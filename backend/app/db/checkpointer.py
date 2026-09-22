"""LangGraph checkpointer 单例。

独立 psycopg3 连接池(与 SQLAlchemy asyncpg 引擎互不相干);
thread_id = str(conversation_id),agent 多轮上下文存这里。
"""
import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text

from app.core.config import get_settings
from app.db.base import engine

logger = logging.getLogger(__name__)

_saver: AsyncPostgresSaver | None = None
_ctx = None  # from_conn_string 的异步上下文管理器(持有连接池生命周期,__aenter__ 后常驻)


async def setup_checkpointer() -> AsyncPostgresSaver:
    """lifespan 启动时调用一次。"""
    global _saver, _ctx
    if _saver is not None:
        return _saver

    _ctx = AsyncPostgresSaver.from_conn_string(get_settings().checkpoint_db_dsn)
    saver = await _ctx.__aenter__()
    _saver = saver

    # 已知坑(langgraph issue #2570):全新库直接 setup() 可能报
    # "current transaction is aborted"——先独立事务建 checkpoint_migrations 表再 setup
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE IF NOT EXISTS checkpoint_migrations (v INTEGER PRIMARY KEY)")
        )
    await saver.setup()
    logger.info("LangGraph checkpointer ready (thread_id = conversation_id)")
    return saver


def get_checkpointer() -> AsyncPostgresSaver | None:
    """返回已初始化的 checkpointer(未初始化返回 None,此时不可用)。"""
    return _saver


async def close_checkpointer() -> None:
    """lifespan 关停时调用。"""
    global _saver, _ctx
    if _ctx is not None:
        try:
            await _ctx.__aexit__(None, None, None)
        except Exception:  # 关停阶段吞异常
            logger.warning("checkpointer close failed", exc_info=True)
        _ctx = None
        _saver = None
