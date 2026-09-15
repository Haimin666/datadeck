"""平台模型适配器：优先读取注入的模型目录，环境变量作为部署兜底。"""

from __future__ import annotations

import os

from datadeck.adapters.model_provider import EnvModelProvider
from datadeck.ports.models import ChatModelSpec, ModelCatalog, ModelProvider


class PlatformModelProvider(ModelProvider):
    """将宿主注入的 ModelCatalog 转成核心 ModelProvider。"""

    def __init__(self, catalog: ModelCatalog, *, fallback: EnvModelProvider | None = None):
        self._catalog = catalog
        self._fallback = fallback or EnvModelProvider()

    def get_model_info(self, spec: str) -> ChatModelSpec | None:
        info = self._get_cache_info(spec)
        if info is not None:
            return self._to_spec(info)
        return self._fallback.get_model_info(spec)

    def get_all_specs(self, kind: str = "chat") -> list[ChatModelSpec]:
        specs = [self._to_spec(info) for info in self._get_all_cache_infos(kind)]
        fallback_specs = self._fallback.get_all_specs(kind)
        return [*specs, *(spec for spec in fallback_specs if spec.spec not in {item.spec for item in specs})]

    @staticmethod
    def _to_spec(info) -> ChatModelSpec:
        return ChatModelSpec(
            spec=info.spec,
            provider=info.provider_type,
            model=info.model_id,
            base_url=info.base_url,
            api_key=info.api_key,
            proxy_url=info.proxy_url,
            temperature=float(os.getenv("DATADECK_MODEL_TEMPERATURE", "0.0") or 0.0),
        )

    def _get_cache_info(self, spec: str):
        return self._catalog.get_model_info(spec)

    def _get_all_cache_infos(self, kind: str):
        return self._catalog.get_all_specs(kind)
