"""Steer 中间件（自 Yuxi agents/middlewares/steer.py 抽离）。

平台依赖 ``yuxi.services.agent_request_queue_service`` 收敛为可注入的
``should_end`` 回调（默认永不结束），保持 Steer 生命周期边界语义。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, hook_config


class SteerMiddleware(AgentMiddleware):
    def __init__(self, should_end: Callable[[object], Awaitable[bool]] | None = None) -> None:
        super().__init__()
        self._should_end = should_end

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):  # noqa: ARG002
        return await self._jump_if_should_end(runtime)

    @hook_config(can_jump_to=["end"])
    async def aafter_model(self, state, runtime):
        """兜底处理无工具模型轮次，避免 Steer 落在最后一次检查之后。"""
        if _last_message_has_tool_calls(state):
            return None
        return await self._jump_if_should_end(runtime)

    async def _jump_if_should_end(self, runtime):
        if self._should_end is None:
            return None
        try:
            end = await self._should_end(runtime)
        except Exception:  # noqa: BLE001
            return None
        return {"jump_to": "end"} if end else None


def _last_message_has_tool_calls(state) -> bool:
    messages = state.get("messages") if isinstance(state, dict) else None
    if not messages:
        return False
    last_message = messages[-1]
    if isinstance(last_message, dict):
        return bool(last_message.get("tool_calls"))
    return bool(getattr(last_message, "tool_calls", None))