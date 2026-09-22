"""模型注册中心:多平台(OpenAI 兼容)统一管理,懒构建 + LRU 缓存 ChatOpenAI。

B3 改造:供应商不再来自静态 .env,而是由 WebUI 写入 providers 表。
ModelRegistry 持有一份「provider_name -> 配置」的映射,提供 set_providers() 供
providers API / models API 在 DB 变更后刷新。模型 id 全局唯一 = f"{provider_name}::{model_id}"。
"""
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

# 模型 id 分隔符:provider_name::model_id
ID_SEP = "::"

# 内置供应商:由 .env 的 OPENAI_API_KEY / OPENAI_API_BASE / BUILTIN_MODELS 构造,不落库。
# 模型分组固定显示为 BUILTIN_PROVIDER_NAME,组内**只展示 BUILTIN_MODELS 里列出的模型**
# (不再从 {base_url}/models 自动拉取),DEFAULT_MODEL 为默认选中项。
BUILTIN_PROVIDER_NAME = "内置模型"
BUILTIN_DEFAULT_BASE_URL = "https://api.openai.com/v1"


@dataclass(frozen=True)
class ModelSpec:
    """一个模型:标识 + 上下文规格(输入 / 输出 token 上限)。

    为 None 表示未配置,消费方回退到默认值。
    """

    id: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def _positive_int(raw: str) -> int | None:
    """把配置里的数字段解析为正整数;非法或非正数返回 None。"""
    text = (raw or "").strip().replace("_", "").replace(",", "")
    if not text.isdigit():
        return None
    value = int(text)
    return value if value > 0 else None


def parse_model_specs(raw: str) -> list[ModelSpec]:
    """解析 `BUILTIN_MODELS`:`模型名[:输入上下文[:输出上下文]]`,逗号分隔。

    数字段从**右侧**识别,所以模型名里含 `:` 也不会被误切
    (例:`deepseek-v4.1-flash:1000000:384000`)。
    """
    specs: list[ModelSpec] = []
    seen: set[str] = set()
    for chunk in (raw or "").replace(";", ",").split(","):
        if not chunk.strip():
            continue
        parts = [p.strip() for p in chunk.strip().split(":")]
        # 从右往左收集连续的数字段(最多两个=输入/输出),剩下的才是模型名
        nums: list[int] = []
        while len(parts) > 1 and len(nums) < 2:
            value = _positive_int(parts[-1])
            if value is None:
                break
            nums.insert(0, value)
            parts = parts[:-1]
        # 只给一个数字时视为「输入上下文」(最常见用法),给两个时依次为「输入 / 输出」
        input_tokens = nums[0] if nums else None
        output_tokens = nums[1] if len(nums) > 1 else None
        mid = ":".join(parts).strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        specs.append(ModelSpec(id=mid, input_tokens=input_tokens, output_tokens=output_tokens))
    return specs


def model_specs_from_rows(models: list | None, extra_models: list | None = None) -> list[ModelSpec]:
    """把 DB 的 `provider.models` / `provider.extra_models` 归一为 ModelSpec 列表。

    兼容历史形态:
    - `{"id", "display_name"}`(早期自动同步结果,无上下文)
    - `{"id", "input_tokens", "output_tokens"}`(当前手动录入)
    - `["模型名"]`(旧 `extra_models` 字符串列表)
    """
    specs: list[ModelSpec] = []
    seen: set[str] = set()
    for item in list(models or []) + list(extra_models or []):
        if isinstance(item, dict):
            mid = str(item.get("id") or "").strip()
            input_tokens = _positive_int(str(item.get("input_tokens") or ""))
            output_tokens = _positive_int(str(item.get("output_tokens") or ""))
        else:
            mid = str(item or "").strip()
            input_tokens = output_tokens = None
        if not mid or mid in seen:
            continue
        seen.add(mid)
        specs.append(ModelSpec(id=mid, input_tokens=input_tokens, output_tokens=output_tokens))
    return specs


def builtin_provider(
    api_key: str,
    base_url: str,
    default_model: str,
    specs: list[ModelSpec] | None = None,
) -> dict | None:
    """由 .env 构造内置供应商配置;DEFAULT_MODEL 为空时返回 None(不注入)。

    模型列表来自 `BUILTIN_MODELS`;DEFAULT_MODEL 不在其中时补到最前,保证默认模型一定可选。
    """
    model = (default_model or "").strip()
    if not model:
        return None
    models = list(specs or [])
    if not any(spec.id == model for spec in models):
        models.insert(0, ModelSpec(id=model))
    return {
        "name": BUILTIN_PROVIDER_NAME,
        "provider_type": "openai",
        "base_url": (base_url or "").strip() or BUILTIN_DEFAULT_BASE_URL,
        "api_key": (api_key or "").strip(),
        "models": models,
    }


def builtin_model_id(default_model: str) -> str:
    """内置供应商默认模型的全局 id(前端用作默认选中项)。"""
    return f"{BUILTIN_PROVIDER_NAME}{ID_SEP}{(default_model or '').strip()}"


class ModelError(Exception):
    """带错误码的模型/平台异常,API 层转 SSE error 事件或 HTTP 4xx。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class ChatOpenAICompat(ChatOpenAI):
    """OpenAI 兼容扩展:把流式增量里的 reasoning_content(DeepSeek 思考流)
    转发到 message.additional_kwargs,供 bridge 输出 reasoning 事件。
    其他平台无此字段,行为零影响。"""

    def _convert_chunk_to_generation_chunk(
        self, chunk, default_chunk_class, base_generation_info
    ):
        gen = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen is not None:
            try:
                choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
                delta = (choices[0] or {}).get("delta") or {}
                rc = delta.get("reasoning_content")
                if rc:
                    gen.message.additional_kwargs["reasoning_content"] = rc
            except Exception:  # noqa: BLE001  解析失败不影响主流程
                pass
        return gen


@dataclass
class ModelInfo:
    id: str  # provider_name::model_id(全局唯一)
    provider: str  # provider 实例名称
    display_name: str  # 模型名
    available: bool  # provider 的 API key 是否已填(前端置灰/过滤)
    input_tokens: int | None = None  # 输入上下文上限(前端使用率圆圈的分母)
    output_tokens: int | None = None  # 最大输出 token 数


@dataclass
class ProviderRuntime:
    """DB providers 表的一行在 registry 内的运行时形态。"""

    name: str
    provider_type: str
    base_url: str
    api_key: str
    models: list[ModelSpec] = field(default_factory=list)


class ModelRegistry:
    def __init__(self, providers: list[dict] | None = None, capacity: int = 32):
        self._by_name: dict[str, ProviderRuntime] = {}
        self._lru: OrderedDict[str, ChatOpenAI] = OrderedDict()
        self._capacity = capacity
        if providers:
            self.set_providers(providers)

    def set_providers(self, providers: list[dict]) -> None:
        """全量刷新 provider 映射(DB 变更后调用);清空 chat model 缓存。"""
        self._by_name = {}
        for p in providers:
            name = str(p.get("name") or "").strip()
            if not name:
                continue
            raw_models = p.get("models") or []
            specs = (
                list(raw_models)
                if raw_models and isinstance(raw_models[0], ModelSpec)
                else model_specs_from_rows(raw_models, p.get("extra_models"))
            )
            self._by_name[name] = ProviderRuntime(
                name=name,
                provider_type=str(p.get("provider_type") or "custom"),
                base_url=str(p.get("base_url") or ""),
                api_key=str(p.get("api_key") or ""),
                models=specs,
            )
        # base_url/api_key 可能变化,旧 chat model 缓存作废
        self._lru.clear()
        logger.info("ModelRegistry 刷新: %d 个 provider", len(self._by_name))

    def list_models(self) -> list[ModelInfo]:
        items: list[ModelInfo] = []
        for name, p in self._by_name.items():
            for spec in p.models:
                items.append(
                    ModelInfo(
                        id=f"{name}{ID_SEP}{spec.id}",
                        provider=name,
                        display_name=spec.id,
                        available=bool(p.api_key),
                        input_tokens=spec.input_tokens,
                        output_tokens=spec.output_tokens,
                    )
                )
        return items

    def spec(self, model_id: str) -> ModelSpec | None:
        """按全局 id 取模型规格;找不到返回 None。"""
        provider_name, sep, mid = model_id.partition(ID_SEP)
        if not sep:
            return None
        p = self._by_name.get(provider_name)
        if p is None:
            return None
        return next((spec for spec in p.models if spec.id == mid), None)

    def context_window(self, model_id: str, default: int) -> int:
        """模型声明的输入上下文上限;未配置时回退 default。

        自动压缩阈值与前端上下文圆圈的"分母"都取它 —— 1M 上下文的模型
        不该按 128k 的默认值去触发压缩。
        """
        spec = self.spec(model_id)
        if spec is not None and spec.input_tokens:
            return spec.input_tokens
        return default

    def get_chat_model(self, model_id: str) -> ChatOpenAI:
        provider_name, sep, mid = model_id.partition(ID_SEP)
        if not sep:
            raise ModelError("model_not_found", f"模型 {model_id} 未配置")
        p = self._by_name.get(provider_name)
        if p is None:
            raise ModelError("model_not_found", f"供应商 {provider_name} 不存在或已删除")
        if not any(spec.id == mid for spec in p.models):
            raise ModelError(
                "model_not_found",
                f"模型 {mid} 不在供应商 {provider_name} 的模型列表中,请在设置里添加该模型",
            )
        if not p.api_key:
            raise ModelError("provider_key_missing", f"供应商 {provider_name} 未填写 API Key")
        if model_id in self._lru:
            self._lru.move_to_end(model_id)
            return self._lru[model_id]
        model = ChatOpenAICompat(
            model=mid,
            base_url=p.base_url,
            api_key=p.api_key,
            temperature=0.7,
            timeout=60,
            max_retries=1,
            streaming=True,  # 必须显式开,否则 on_chat_model_stream 不触发
        )
        self._lru[model_id] = model
        self._lru.move_to_end(model_id)
        if len(self._lru) > self._capacity:
            self._lru.popitem(last=False)
        return model


# 模块级单例(与 deps 共用;DB 变更时通过 set_providers 刷新)
registry = ModelRegistry()
