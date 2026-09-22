"""供应商(Provider)管理:CRUD + 调用 {base_url}/models 自动同步模型列表(按用户隔离)。

WebUI 用户自助添加 LLM 平台(OpenAI/Anthropic/DeepSeek 等),替代静态 .env 配置。
"""
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.registry import BUILTIN_PROVIDER_NAME
from app.db.base import get_session
from app.db.models import Provider, User
from app.schemas.provider import (
    ProviderCreate,
    ProviderList,
    ProviderModelItem,
    ProviderOut,
    ProviderSyncOut,
    ProviderUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["providers"])

# 模型请求超时(秒)
MODEL_SYNC_TIMEOUT = 30


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}****{key[-4:]}"


def _to_out(p: Provider) -> ProviderOut:
    return ProviderOut(
        id=p.id,
        name=p.name,
        provider_type=p.provider_type,
        base_url=p.base_url,
        api_key_masked=_mask_key(p.api_key),
        models=[ProviderModelItem(**m) if isinstance(m, dict) else ProviderModelItem(id=str(m)) for m in (p.models or [])],
        extra_models=list(p.extra_models or []),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


async def _get_or_404(session: AsyncSession, provider_id: uuid.UUID, user_id: uuid.UUID) -> Provider:
    p = await session.scalar(
        select(Provider).where(Provider.id == provider_id, Provider.user_id == user_id)
    )
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "provider_not_found", "message": "供应商不存在"})
    return p


async def _fetch_models(base_url: str, api_key: str) -> tuple[list[ProviderModelItem], str]:
    """调用 OpenAI 兼容 GET {base_url}/models 查询模型列表。

    部分平台(如 API2D、硅基流动)的兼容端点要求 /v1 前缀:用户填 base_url 时
    可能省略 /v1,导致 /models 返回 403/404。这里自动 fallback:
    先试 {base_url}/models,失败再试 {base_url}/v1/models。
    返回 (模型列表, 实际生效的 base_url),后者用于规范化存储,保证 chat 同端点。
    """
    candidates: list[str] = []
    seen: set[str] = set()
    for c in (base_url.rstrip("/"), f"{base_url.rstrip('/')}/v1"):
        if c not in seen:
            seen.add(c)
            candidates.append(c)

    items: list[ProviderModelItem] = []
    effective = base_url.rstrip("/")
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=MODEL_SYNC_TIMEOUT) as client:
        for cand in candidates:
            url = f"{cand}/models"
            headers = {"Authorization": f"Bearer {api_key}"}
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    raw = data.get("data", data if isinstance(data, list) else [])
                    model_items: list[ProviderModelItem] = []
                    model_ids: set[str] = set()
                    for m in raw:
                        if not isinstance(m, dict):
                            continue
                        mid = str(m.get("id") or "").strip()
                        if not mid or mid in model_ids:
                            continue
                        model_ids.add(mid)
                        model_items.append(
                            ProviderModelItem(id=mid, display_name=str(m.get("display_name") or m.get("id") or mid))
                        )
                    items = model_items
                    effective = cand
                    return items, effective
                # 403/404:路径不对(缺 /v1)或鉴权失败,继续尝试下一个候选
                if resp.status_code in (401, 403, 404, 405):
                    last_err = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                    continue
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                last_err = e
            except Exception as e:  # noqa: BLE001
                last_err = e
    if last_err is not None:
        raise last_err
    return items, effective


@router.get("", response_model=ProviderList)
async def list_providers(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ProviderList:
    rows = await session.scalars(
        select(Provider).where(Provider.user_id == user.id).order_by(Provider.created_at.asc())
    )
    items = [_to_out(p) for p in rows.all()]
    return ProviderList(items=items, total=len(items))


@router.post("", status_code=201, response_model=ProviderOut)
async def create_provider(
    body: ProviderCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ProviderOut:
    if body.name.strip() == BUILTIN_PROVIDER_NAME:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "provider_name_reserved",
                "message": f"「{BUILTIN_PROVIDER_NAME}」是内置供应商的保留名称,请换一个实例名",
            },
        )
    exists = await session.scalar(
        select(Provider).where(Provider.user_id == user.id, Provider.name == body.name)
    )
    if exists is not None:
        raise HTTPException(status_code=409, detail={"code": "provider_name_exists", "message": "实例名称已存在"})
    p = Provider(
        user_id=user.id,
        name=body.name,
        provider_type=body.provider_type,
        base_url=body.base_url,
        api_key=body.api_key,
        # 模型完全由用户手动录入(名称 + 输入/输出上下文),不再自动同步
        models=[m.model_dump(exclude_none=True) for m in body.models],
        extra_models=[],
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return _to_out(p)


@router.put("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ProviderOut:
    p = await _get_or_404(session, provider_id, user.id)
    if body.name is not None and body.name != p.name:
        if body.name.strip() == BUILTIN_PROVIDER_NAME:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "provider_name_reserved",
                    "message": f"「{BUILTIN_PROVIDER_NAME}」是内置供应商的保留名称,请换一个实例名",
                },
            )
        exists = await session.scalar(
            select(Provider).where(Provider.user_id == user.id, Provider.name == body.name)
        )
        if exists is not None:
            raise HTTPException(status_code=409, detail={"code": "provider_name_exists", "message": "实例名称已存在"})
        p.name = body.name
    if body.provider_type is not None:
        p.provider_type = body.provider_type
    if body.base_url is not None:
        p.base_url = body.base_url
    if body.api_key is not None:
        p.api_key = body.api_key
    if body.models is not None:
        p.models = [m.model_dump(exclude_none=True) for m in body.models]
        p.extra_models = []  # 旧字段仅作历史兼容,新流程不再使用
    p.updated_at = func.now()
    await session.commit()
    await session.refresh(p)
    return _to_out(p)


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    p = await _get_or_404(session, provider_id, user.id)
    await session.delete(p)
    await session.commit()


@router.post("/{provider_id}/sync", response_model=ProviderSyncOut)
async def sync_provider_models(
    provider_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ProviderSyncOut:
    """探测端点上的候选模型名(**只返回,不写库**)。

    模型清单已改为手动录入(名称 + 输入/输出上下文),这里仅作「发现」辅助:
    前端把返回的模型名填进表单,由用户补全上下文后保存。
    顺带规范化 base_url(兼容缺 /v1 的平台,如 API2D)。
    """
    p = await _get_or_404(session, provider_id, user.id)
    if not p.api_key:
        raise HTTPException(status_code=400, detail={"code": "provider_key_missing", "message": "请先填写 API Key"})
    try:
        items, effective = await _fetch_models(p.base_url, p.api_key)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "sync_failed",
                "message": f"查询模型失败(HTTP {e.response.status_code}):请检查 base_url / API Key",
            },
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"code": "sync_failed", "message": f"查询模型失败:{e}"})
    if effective != p.base_url.rstrip("/"):
        p.base_url = effective
        p.updated_at = func.now()
        await session.commit()
        await session.refresh(p)
    return ProviderSyncOut(
        models=items,
        message=f"发现 {len(items)} 个模型(未写入,请补全输入/输出上下文后保存)",
    )
