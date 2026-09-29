"""FastAPI 依赖:模块级单例注入 + DB 驱动的 registry 刷新 + 用户认证。"""
import uuid
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.manager import AgentManager
from app.core.config import get_settings
from app.core.registry import ModelRegistry, builtin_provider, parse_model_specs, registry
from app.core.security import verify_token
from app.db.base import get_session
from app.db.models import Provider, User

_agent_manager: AgentManager | None = None

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    """从 Authorization: Bearer <token> 解析当前用户。"""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "请先登录"})
    user_id = verify_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(status_code=401, detail={"code": "token_invalid", "message": "登录已过期,请重新登录"})
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail={"code": "token_invalid", "message": "登录已过期,请重新登录"})
    user = await session.get(User, uid)
    if user is None:
        raise HTTPException(status_code=401, detail={"code": "token_invalid", "message": "登录已过期,请重新登录"})
    return user


async def get_registry(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ModelRegistry:
    """每次请求从 DB 全量刷新当前用户的 provider 映射,保证动态配置即时生效。

    内置供应商(来自 .env)排在最前,保证 DEFAULT_MODEL 是前端的默认选中项。
    """
    rows = await session.scalars(
        select(Provider).where(Provider.user_id == user.id).order_by(Provider.created_at.asc())
    )
    settings = get_settings()
    providers: list[dict] = []
    builtin = builtin_provider(
        settings.openai_api_key,
        settings.openai_api_base,
        settings.default_model,
        parse_model_specs(settings.builtin_models),
    )
    if builtin is not None:
        providers.append(builtin)
    for p in rows.all():
        providers.append(
            {
                "name": p.name,
                "provider_type": p.provider_type,
                "base_url": p.base_url,
                "api_key": p.api_key,
                # 原样传入,由 model_specs_from_rows 归一(含输入/输出上下文)
                "models": p.models or [],
                "extra_models": p.extra_models or [],
            }
        )
    registry.set_providers(providers)
    return registry


def get_agent_manager() -> AgentManager:
    """懒构建:首个请求到来时 checkpointer 已由 lifespan 初始化。"""
    global _agent_manager
    if _agent_manager is None:
        from app.db.checkpointer import get_checkpointer

        _agent_manager = AgentManager(
            registry=registry,
            checkpointer=get_checkpointer(),
            image_dir=Path(get_settings().image_dir),
            code_exec_timeout=get_settings().code_exec_timeout,
            tavily_api_key=get_settings().tavily_api_key,
            loop_window=get_settings().tool_loop_guard_window,
            loop_repeats=get_settings().tool_loop_guard_repeats,
            computer_use_enabled=get_settings().computer_use_enabled,
            computer_use_max_actions=get_settings().computer_use_max_actions,
            computer_use_action_interval=get_settings().computer_use_action_interval,
            computer_use_max_width=get_settings().computer_use_max_image_width,
            computer_use_image_format=get_settings().computer_use_image_format,
            computer_use_jpeg_quality=get_settings().computer_use_jpeg_quality,
            # 上下文预算工具与自动压缩共用同一组数值(默认窗口 / 触发比例)
            context_window_default=get_settings().context_window_default,
            compact_trigger_fraction=get_settings().compact_trigger_fraction,
        )
    return _agent_manager
