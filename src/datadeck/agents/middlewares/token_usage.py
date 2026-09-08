"""Token 用量观测中间件（自 Yuxi agents/middlewares/token_usage.py 抽离，精简）。

去掉供应商分桶/缓存，保留核心：每次主模型调用后估算近似上下文占用并写入 state。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, TypedDict

from langchain.agents.middleware.types import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import Command


class TokenUsagePayload(TypedDict, total=False):
    state_message_count: int
    llm_message_count: int
    llm_message_tokens: int
    context_window: int | None
    context_usage_ratio: float | None
    total_tokens: int
    estimated_total_tokens: int
    measured_at: str
    latest: dict[str, Any] | None


class TokenUsageState(AgentState):
    token_usage: TokenUsagePayload


class TokenUsageMiddleware(AgentMiddleware[TokenUsageState]):
    state_schema = TokenUsageState

    def __init__(self, token_counter=count_tokens_approximately) -> None:
        super().__init__()
        self.token_counter = token_counter

    def wrap_model_call(self, request: ModelRequest,
                        handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        response = handler(request)
        return self._record(request, response)

    async def awrap_model_call(self, request: ModelRequest,
                               handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelResponse:
        response = await handler(request)
        return self._record(request, response)

    def _record(self, request: ModelRequest, response: ModelResponse) -> ModelResponse:
        from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse as MR
        from langgraph.types import Command as Cmd

        msgs = list(request.messages or [])
        llm_msgs = [m for m in msgs if _is_llm_visible(m)]
        llm_tokens = self.token_counter(llm_msgs)
        now = datetime.now(UTC).isoformat()

        payload: TokenUsagePayload = {
            "state_message_count": len(msgs),
            "llm_message_count": len(llm_msgs),
            "llm_message_tokens": llm_tokens,
            "estimated_total_tokens": llm_tokens,
            "measured_at": now,
            "total_tokens": _response_total_tokens(response),
        }
        ctx_window = _context_window(request)
        if ctx_window:
            payload["context_window"] = ctx_window
            payload["context_usage_ratio"] = round(llm_tokens / ctx_window, 4)

        update = {"token_usage": payload}
        if isinstance(response, ExtendedModelResponse):
            existing = dict(response.command.update) if response.command is not None else {}
            existing.update(update)
            return ExtendedModelResponse(model_response=response.model_response, command=Cmd(update=existing))
        # 普通 ModelResponse 不接受 command 参数；协议要求用 ExtendedModelResponse 包装
        return ExtendedModelResponse(model_response=response, command=Cmd(update=update))


# ── 小助手 ────────────────────────────────────────────────


def _is_llm_visible(m) -> bool:
    """工具结果以外的消息计入 LLM 上下文（工具结果是否计由 summary 决定）。"""
    from langchain_core.messages import ToolMessage
    return not isinstance(m, ToolMessage)


def _response_total_tokens(response) -> int:
    for msg in [getattr(response, "result", None) or []]:
        if isinstance(msg, AIMessage):
            usage = getattr(msg, "usage_metadata", None) or {}
            total = (usage.get("total_tokens") or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)))
            if isinstance(total, int):
                return total
    return 0


def _context_window(request) -> int | None:
    ctx = getattr(request.model, "max_context_tokens", None)
    if isinstance(ctx, int) and ctx > 0:
        return ctx
    return None