"""GET /api/models:可用模型列表(动态聚合内置供应商 + 当前用户已配置供应商)。"""
from fastapi import APIRouter, Depends

from app.api.deps import get_registry
from app.core.config import get_settings
from app.core.registry import ModelRegistry, builtin_model_id

router = APIRouter(tags=["models"])


@router.get("/models")
async def list_models(registry: ModelRegistry = Depends(get_registry)) -> dict:
    """返回模型列表;default_model 为 .env DEFAULT_MODEL 的全局 id(前端默认选中)。

    内置供应商的模型列表由 get_registry 解析(含 TTL 缓存),这里只负责拼 default_model。
    """
    settings = get_settings()
    has_builtin = bool((settings.default_model or "").strip())
    return {
        "models": registry.list_models(),
        "default_model": builtin_model_id(settings.default_model) if has_builtin else "",
    }
