"""供应商(Provider)API 模型。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProviderModelItem(BaseModel):
    """手动录入的模型:名称 + 输入/输出上下文规格。

    模型清单不再从 `{base_url}/models` 自动同步,由用户显式声明,
    上下文数值用于前端使用率圆圈与自动压缩阈值。
    """

    id: str = Field(min_length=1, max_length=200)
    display_name: str | None = None
    input_tokens: int | None = Field(default=None, gt=0, description="输入上下文上限")
    output_tokens: int | None = Field(default=None, gt=0, description="最大输出 token 数")


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="实例名称,如「我的 DeepSeek」")
    provider_type: str = Field(
        default="custom",
        description="供应商类型: openai/anthropic/deepseek/qwen/zhipu/moonshot/custom",
    )
    base_url: str = Field(min_length=1, max_length=500, description="OpenAI 兼容端点")
    api_key: str = Field(default="", max_length=500, description="API Key")
    models: list[ProviderModelItem] = Field(
        default_factory=list,
        description="手动录入的模型:名称 + 输入/输出上下文规格(不再从 /models 自动同步)",
    )


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    provider_type: str | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    models: list[ProviderModelItem] | None = Field(default=None, description="覆盖模型清单(名称 + 上下文)")


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    provider_type: str
    base_url: str
    api_key_masked: str = ""  # 不回传明文,仅展示掩码
    models: list[ProviderModelItem] = Field(default_factory=list)
    extra_models: list[str] = Field(default_factory=list)  # 仅历史兼容,新流程不再写入
    created_at: datetime
    updated_at: datetime


class ProviderList(BaseModel):
    items: list[ProviderOut]
    total: int


class ProviderSyncOut(BaseModel):
    """模型「发现」结果(不写库)。"""

    models: list[ProviderModelItem]
    message: str = ""
