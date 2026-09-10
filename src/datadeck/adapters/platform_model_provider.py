"""平台模型适配器：优先读取宿主模型缓存，环境变量作为部署兜底。"""

from __future__ import annotations

import os

from datadeck.adapters.model_provider import EnvModelProvider
from datadeck.ports.models import ChatModelSpec, ModelProvider


class PlatformModelProvider(ModelProvider):
    """适配 `server.services.model_providers.cache.model_cache`。"""

    def __init__(self, *, fallback: EnvModelProvider | None = None):
        self._fallback = fallback or EnvModelProvider()

    def get_model_info(self, spec: str) -> ChatModelSpec | None:
        info = self._get_cache_info(spec)
        if info is not None:
            return ChatModelSpec(
                spec=info.spec,
                provider=info.provider_type,
                model=info.model_id,
                base_url=info.base_url,
                api_key=info.api_key,
                temperature=float(os.getenv("DATADECK_MODEL_TEMPERATURE", "0.0") or 0.0),
            )
        return self._fallback.get_model_info(spec)

    def get_all_specs(self, kind: str = "chat") -> list[ChatModelSpec]:
        specs = [
            ChatModelSpec(
                spec=info.spec,
                provider=info.provider_type,
                model=info.model_id,
                base_url=info.base_url,
                api_key=info.api_key,
                temperature=float(os.getenv("DATADECK_MODEL_TEMPERATURE", "0.0") or 0.0),
            )
            for info in self._get_all_cache_infos(kind)
        ]
        fallback_specs = self._fallback.get_all_specs(kind)
        return [*specs, *(spec for spec in fallback_specs if spec.spec not in {item.spec for item in specs})]

    @staticmethod
    def _get_cache_info(spec: str):
        from server.services.model_providers.cache import model_cache

        return model_cache.get_model_info(spec)

    @staticmethod
    def _get_all_cache_infos(kind: str):
        from server.services.model_providers.cache import model_cache

        return model_cache.get_all_specs(kind)
