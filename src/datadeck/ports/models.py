"""ModelProvider: 把模型 spec 解析为 langchain chat model 的适配者接口。

对应 Yuxi 的 ``yuxi.models.providers.cache`` / ``yuxi.agents.models``。
下游依赖（checkpointer、memory）用 ``ModelProvider`` 取回可用的聊天模型。
"""

from __future__ import annotations

import os

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from langchain.chat_models import BaseChatModel


@dataclass(frozen=True)
class ChatModelSpec:
    """一个可解析的聊天模型规格。"""

    spec: str                 # 形如 "openai:gpt-4o" / "anthropic:claude-3-7-sonnet"
    provider: str             # openai | anthropic | gemini
    model: str                # model_id
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.0


@runtime_checkable
class ModelProvider(Protocol):
    """把 spec 解析为 BaseChatModel。"""

    def get_model_info(self, spec: str) -> ChatModelSpec | None:
        """解析 spec；不可识别返回 None。"""

    def get_all_specs(self, kind: str = "chat") -> list[ChatModelSpec]:
        """枚举可用模型（错误提示用）。"""


def load_chat_model(spec: ChatModelSpec, provider: ModelProvider, **kwargs) -> BaseChatModel:
    """从适配者提供的模型规格构建聊天模型（对应 Yuxi agents.models.load_chat_model）。"""
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    if spec.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=spec.model,
            api_key=SecretStr(spec.api_key),
            base_url=spec.base_url or None,
            temperature=spec.temperature,
            **kwargs,
        )
    # 默认 openai 兼容
    model_kwargs = {
        "model": spec.model,
        "api_key": SecretStr(spec.api_key),
        "base_url": spec.base_url or None,
        "temperature": spec.temperature,
        "stream_usage": True,
    }
    model_kwargs.update(kwargs)
    proxy = os.getenv("DATADECK_MODEL_HTTP_PROXY")
    if proxy and spec.base_url:
        import httpx

        model_kwargs["http_client"] = httpx.Client(proxy=proxy, trust_env=False)
        model_kwargs["http_async_client"] = httpx.AsyncClient(proxy=proxy, trust_env=False)
    return ChatOpenAI(**model_kwargs)
