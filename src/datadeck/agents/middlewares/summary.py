"""Summary 中间件（datadeck 版）。

使用 langchain 公开的 ``SummarizationMiddleware``，去掉 Yuxi 对 deepagents
私有 ``_DeepAgentsSummarizationMiddleware`` 的依赖，无需专用 backend。
"""

from __future__ import annotations

from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages.utils import count_tokens_approximately

from datadeck.agents.context import (
    DEFAULT_DATADECK_SUMMARY_PROMPT,
    DEFAULT_SUMMARY_KEEP_MESSAGES,
    DEFAULT_SUMMARY_THRESHOLD_K,
)


def create_summary_middleware(
    model,
    *,
    trigger_k: int | None = None,
    keep_messages: int | None = None,
    summary_prompt: str | None = None,
):
    """构建 langchain 官方摘要中间件（无需 backend）。

    model: str | BaseChatModel —— 摘要用的模型（str 也可，由 create_agent 统一解析）。
    """
    trigger_k = trigger_k if trigger_k is not None else DEFAULT_SUMMARY_THRESHOLD_K
    keep = keep_messages if keep_messages is not None else DEFAULT_SUMMARY_KEEP_MESSAGES
    prompt = summary_prompt or DEFAULT_DATADECK_SUMMARY_PROMPT
    return SummarizationMiddleware(
        model=model,
        trigger=("tokens", int(trigger_k) * 1024),
        keep=("messages", int(keep)),
        token_counter=count_tokens_approximately,
        summary_prompt=prompt,
    )