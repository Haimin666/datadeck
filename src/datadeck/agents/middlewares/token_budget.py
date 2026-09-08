"""Token 预算控制中间件（阶段二 2.4）：从"记录"升级为"控制"。

- 软顶（budget）：每次模型调用前估算消息 token；超预算裁掉中段最老的消息（保 system+首末）。
- 硬顶（hard_limit）：超出硬顶直接裁到硬顶以下，绝不放行；防多轮累计把上下文撑爆。
- 与 SummarizationMiddleware 互补：summary 是"压缩"，本中间件是"保险丝"。
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langchain.agents.middleware.types import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

DEFAULT_BUDGET_TOKENS = 60_000
DEFAULT_HARD_LIMIT_TOKENS = 100_000


class TokenBudgetState(AgentState):
    token_budget_trimmed: int  # 累计被裁消息数（审计用）


class TokenBudgetPayload(TypedDict, total=False):
    before_tokens: int
    after_tokens: int
    trimmed_messages: int
    action: str  # none / trimmed / capped


class TokenBudgetMiddleware(AgentMiddleware[TokenBudgetState]):
    state_schema = TokenBudgetState

    def __init__(
        self,
        budget_tokens: int | None = None,
        hard_limit_tokens: int | None = None,
        token_counter=count_tokens_approximately,
    ) -> None:
        super().__init__()
        self.budget = budget_tokens or int(
            os.getenv("DATADECK_TOKEN_BUDGET", str(DEFAULT_BUDGET_TOKENS)))
        self.hard_limit = hard_limit_tokens or int(
            os.getenv("DATADECK_TOKEN_HARD_LIMIT", str(DEFAULT_HARD_LIMIT_TOKENS)))
        self.token_counter = token_counter

    def wrap_model_call(self, request: ModelRequest,
                        handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        trimmed, payload = self._trim(request.messages)
        if trimmed is not None:
            request.messages = trimmed
        response = handler(request)
        return self._attach(response, payload)

    async def awrap_model_call(self, request: ModelRequest,
                               handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelResponse:
        trimmed, payload = self._trim(request.messages)
        if trimmed is not None:
            request.messages = trimmed
        response = await handler(request)
        return self._attach(response, payload)

    # ── 裁剪核心 ─────────────────────────────────────────

    def _trim(self, messages: list[BaseMessage]) -> tuple[list[BaseMessage] | None, TokenBudgetPayload]:
        before = self.token_counter(messages)
        payload: TokenBudgetPayload = {"before_tokens": before, "after_tokens": before,
                                        "trimmed_messages": 0, "action": "none"}
        if before <= self.budget:
            return None, payload
        # 软顶超限：从中段（跳过开头的 system 和结尾最近 keep_tail 条）裁最老消息
        trimmed_msgs, count = self._trim_middle(messages, self.budget)
        after = self.token_counter(trimmed_msgs)
        payload.update(after_tokens=after, trimmed_messages=count, action="trimmed")
        # 硬顶仍超：激进裁剪到硬限内（只保 system + 最后 keep_tail 条）
        if after > self.hard_limit:
            trimmed_msgs, count2 = self._trim_hard(trimmed_msgs)
            after = self.token_counter(trimmed_msgs)
            payload.update(after_tokens=after,
                          trimmed_messages=count + count2, action="capped")
        return trimmed_msgs, payload

    def _trim_middle(self, messages: list[BaseMessage], target: int,
                     keep_tail: int = 6) -> tuple[list[BaseMessage], int]:
        """保开头 system 与结尾 keep_tail 条，中段从老到新裁剪直到达标。"""
        head_end = 0
        for i, m in enumerate(messages):
            if isinstance(m, SystemMessage):
                head_end = i + 1
        tail_start = max(head_end, len(messages) - keep_tail)
        head = messages[:head_end]
        tail = messages[tail_start:]
        middle = messages[head_end:tail_start]

        removed = 0
        while middle and self.token_counter(head + middle + tail) > target:
            middle.pop(0)  # 最老的先裁
            removed += 1
        return head + middle + tail, removed

    def _trim_hard(self, messages: list[BaseMessage], keep_tail: int = 4) -> tuple[list[BaseMessage], int]:
        head_end = 0
        for i, m in enumerate(messages):
            if isinstance(m, SystemMessage):
                head_end = i + 1
        head = messages[:head_end]
        tail = messages[-keep_tail:]
        removed = len(messages) - len(head) - len(tail)
        return head + tail, max(removed, 0)

    # ── state 记录 ───────────────────────────────────────

    def _attach(self, response: ModelResponse, payload: TokenBudgetPayload) -> ModelResponse:
        from langchain.agents.middleware.types import ExtendedModelResponse
        from langgraph.types import Command

        if payload.get("action") == "none":
            return response
        update: dict[str, Any] = {"token_budget_trimmed": payload["trimmed_messages"]}
        if isinstance(response, ExtendedModelResponse):
            existing = dict(response.command.update) if response.command is not None else {}
            existing.update(update)
            return ExtendedModelResponse(model_response=response.model_response,
                                         command=Command(update=existing))
        return ExtendedModelResponse(model_response=response, command=Command(update=update))
