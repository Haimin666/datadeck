"""统一工具调用超时，避免单个外部工具让 Agent 永久停留在运行中。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage


class ToolTimeoutMiddleware(AgentMiddleware):
    """将工具超时转换为模型可理解的可恢复 ToolMessage。"""

    def __init__(self, timeout_seconds: float = 120) -> None:
        super().__init__()
        try:
            value = float(timeout_seconds)
        except (TypeError, ValueError):
            value = 120.0
        self.timeout_seconds = min(max(value, 1.0), 600.0)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        try:
            return await asyncio.wait_for(handler(request), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return ToolMessage(
                content=(f"工具执行超过 {self.timeout_seconds:g} 秒，已停止等待。"
                         "请检查参数、改用其他工具，或向用户说明外部服务暂不可用。"),
                tool_call_id=(request.tool_call or {}).get("id", ""),
                status="error",
            )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        # 同步工具通常由 LangChain 在线程中执行；同步路径没有可靠的取消能力，
        # 交由具体工具自身的 timeout 处理，避免在这里伪造“已停止”。
        return handler(request)


__all__ = ["ToolTimeoutMiddleware"]
