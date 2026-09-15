"""ModelProvider: 把模型 spec 解析为 langchain chat model 的适配者接口。

对应 Yuxi 的 ``yuxi.models.providers.cache`` / ``yuxi.agents.models``。
下游依赖（checkpointer、memory）用 ``ModelProvider`` 取回可用的聊天模型。
"""

from __future__ import annotations

import os

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from langchain.chat_models import BaseChatModel


@dataclass(frozen=True)
class ChatModelSpec:
    """一个可解析的聊天模型规格。"""

    spec: str                 # 形如 "openai:gpt-4o" / "anthropic:claude-3-7-sonnet"
    provider: str             # openai | anthropic | gemini
    model: str                # model_id
    base_url: str = ""
    api_key: str = ""
    proxy_url: str = ""
    temperature: float = 0.0


@runtime_checkable
class ModelProvider(Protocol):
    """把 spec 解析为 BaseChatModel。"""

    def get_model_info(self, spec: str) -> ChatModelSpec | None:
        """解析 spec；不可识别返回 None。"""

    def get_all_specs(self, kind: str = "chat") -> list[ChatModelSpec]:
        """枚举可用模型（错误提示用）。"""


@runtime_checkable
class ModelCatalog(Protocol):
    """宿主模型目录端口；核心层不关心目录由 DB、Redis 还是文件提供。"""

    def get_model_info(self, spec: str) -> Any | None:
        """按 spec 返回具有 ChatModelSpec 所需属性的模型记录。"""

    def get_all_specs(self, kind: str | None = None) -> list[Any]:
        """列出模型记录；kind 由宿主目录解释。"""


def load_chat_model(spec: ChatModelSpec, provider: ModelProvider, **kwargs) -> BaseChatModel:
    """从适配者提供的模型规格构建聊天模型（对应 Yuxi agents.models.load_chat_model）。"""
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    proxy = spec.proxy_url or os.getenv("DATADECK_MODEL_HTTP_PROXY", "").strip()
    import httpx

    # 始终显式关闭环境代理；只有模型规格或环境配置了 proxy_url 时才使用代理。
    http_clients = {
        "http_client": httpx.Client(proxy=proxy, trust_env=False) if proxy
        else httpx.Client(trust_env=False),
        "http_async_client": httpx.AsyncClient(proxy=proxy, trust_env=False) if proxy
        else httpx.AsyncClient(trust_env=False),
    }

    if spec.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=spec.model,
            api_key=SecretStr(spec.api_key),
            base_url=spec.base_url or None,
            temperature=spec.temperature,
            **http_clients,
            **kwargs,
        )
    # 默认 openai 兼容
    model_kwargs = {
        "model": spec.model,
        "api_key": SecretStr(spec.api_key),
        "base_url": spec.base_url or None,
        "temperature": spec.temperature,
        "stream_usage": True,
        "timeout": float(os.getenv("DATADECK_MODEL_TIMEOUT", "90")),
        "max_retries": 0,
    }
    model_kwargs.update(kwargs)
    model_kwargs.update(http_clients)
    return ChatOpenAI(**model_kwargs)
