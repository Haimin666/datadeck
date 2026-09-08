"""模型加载：经 ModelProvider 适配者从 spec 加载聊天模型（自 Yuxi agents/models.py）。
"""

from __future__ import annotations

from datadeck.ports.models import ModelProvider
from datadeck.ports.models import load_chat_model as _load


def resolve_chat_model_spec(model_spec: str | None, *, fallback: str | None = None) -> str:
    """解析空模型配置：请求/fallback 取其一，仍空则报错。"""
    for candidate in (model_spec, fallback):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError("model spec 不能为空")


def load_chat_model(fully_specified_name: str | None, *, provider: ModelProvider, **kwargs):
    """从适配者解析出模型规格并加载 langchain chat model。"""
    spec_name = resolve_chat_model_spec(fully_specified_name)
    info = provider.get_model_info(spec_name)
    if info is None:
        available = [item.spec for item in provider.get_all_specs("chat")]
        raise ValueError(
            f"Unknown model spec: '{spec_name}'. "
            f"Available chat models: {available}"
        )
    return _load(info, provider, **kwargs)


def default_model_spec(provider: ModelProvider) -> str:
    """provider 提供的默认模型 spec（context.model 为空时的兜底）。"""
    specs = provider.get_all_specs("chat")
    if not specs:
        return ""
    return specs[0].spec