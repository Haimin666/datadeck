"""工具结果信任边界：工具返回的是数据，不是 Agent 指令。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import os
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage


def _result_limit(runtime: Any) -> int:
    context = getattr(runtime, "context", None)
    configured = getattr(context, "summary_tool_result_token_limit", None)
    try:
        tokens = int(configured or os.getenv("DATADECK_TOOL_RESULT_TOKEN_LIMIT", "300"))
    except (TypeError, ValueError):
        tokens = 300
    return min(max(tokens, 64), 8000) * 4


def _mark(value: Any, runtime: Any = None) -> Any:
    if not isinstance(value, ToolMessage):
        return value
    content = value.content
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    limit = _result_limit(runtime)
    if len(content) > limit:
        content = content[:limit] + "\n[工具结果已截断；如需更多内容请缩小查询范围。]"
    return value.model_copy(update={
        "content": f"<untrusted_tool_result>\n{content}\n</untrusted_tool_result>"
    })


class ToolResultTrustBoundaryMiddleware(AgentMiddleware):
    """给模型看到的工具结果增加不可执行数据边界。"""

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return _mark(await handler(request), request.runtime)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return _mark(handler(request), request.runtime)


__all__ = ["ToolResultTrustBoundaryMiddleware"]
