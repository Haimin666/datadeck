"""统一工具参数/执行失败反馈与连续失败保护。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

MAX_CONSECUTIVE_TOOL_FAILURES = 5


def _tool_name(request: Any) -> str:
    return str((getattr(request, "tool_call", None) or {}).get("name") or "unknown")


def _tool_call_id(request: Any) -> str:
    return str((getattr(request, "tool_call", None) or {}).get("id") or "tool-error")


def _failure_text(result: Any) -> str:
    if isinstance(result, ToolMessage) and getattr(result, "status", None) == "error":
        content = result.content
        return content if isinstance(content, str) else str(content)
    return ""


class ToolFailureGuardMiddleware(AgentMiddleware):
    """把校验/执行错误反馈给模型，并阻止工具错误无限循环。"""

    @staticmethod
    def _counts(request: Any) -> dict[str, int]:
        context = request.runtime.context
        counts = getattr(context, "_tool_failure_counts", None)
        if not isinstance(counts, dict):
            counts = {}
            setattr(context, "_tool_failure_counts", counts)
        return counts

    def _failed(self, request: Any, message: str) -> ToolMessage:
        name = _tool_name(request)
        counts = self._counts(request)
        count = int(counts.get(name, 0)) + 1
        counts[name] = count
        if count >= MAX_CONSECUTIVE_TOOL_FAILURES:
            raise RuntimeError(
                f"工具 {name} 已连续失败 {count} 次，已终止本次运行。最后错误：{message[:500]}"
            )
        return ToolMessage(
            content=(f"工具 {name} 第 {count} 次调用失败：{message[:500]}。"
                     "请根据工具 schema 修正参数后重试。"),
            tool_call_id=_tool_call_id(request),
            status="error",
        )

    def _handled(self, request: Any, result: Any) -> Any:
        message = _failure_text(result)
        if message:
            return self._failed(request, message)
        self._counts(request).pop(_tool_name(request), None)
        return result

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001
            return self._failed(request, f"{type(exc).__name__}: {exc}")
        return self._handled(request, result)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            result = handler(request)
        except Exception as exc:  # noqa: BLE001
            return self._failed(request, f"{type(exc).__name__}: {exc}")
        return self._handled(request, result)


__all__ = ["MAX_CONSECUTIVE_TOOL_FAILURES", "ToolFailureGuardMiddleware"]
