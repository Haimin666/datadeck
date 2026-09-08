"""进程内内存模型适配者：从环境变量读取单模型。

生产宿主应替换为 Yuxi 式的 DB + Redis 供应商缓存。
"""

from __future__ import annotations

import os

from datadeck.ports.models import ChatModelSpec, ModelProvider


class EnvModelProvider(ModelProvider):
    """从 ``DATADECK_*`` 环境变量解析单个聊天模型。"""

    def __init__(self) -> None:
        spec = os.getenv("DATADECK_MODEL", "openai:gpt-4o")
        provider = os.getenv("DATADECK_MODEL_PROVIDER", "openai")
        model_id = spec.split(":", 1)[-1] if ":" in spec else spec
        self._spec = ChatModelSpec(
            spec=spec,
            provider=provider,
            model=model_id,
            base_url=os.getenv("DATADECK_BASE_URL", ""),
            api_key=os.getenv("DATADECK_API_KEY", ""),
            temperature=float(os.getenv("DATADECK_MODEL_TEMPERATURE", "0.0") or 0.0),
        )

    def get_model_info(self, spec: str) -> ChatModelSpec | None:
        return self._spec if spec == self._spec.spec else None

    def get_all_specs(self, kind: str = "chat") -> list[ChatModelSpec]:
        return [self._spec]