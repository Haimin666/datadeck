"""DataAgent 的取数流程门控：元数据确认和 SQL 自检必须先于执行。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

_RAG_TERMS = ("口径", "定义", "公式", "指标", "逾期率", "怎么算", "含义")


def _requires_rag(state: Any) -> bool:
    messages = state.get("messages") if isinstance(state, dict) else None
    if not messages:
        return False
    for message in reversed(messages):
        if getattr(message, "type", "") == "human":
            content = getattr(message, "content", "")
            return isinstance(content, str) and any(term in content for term in _RAG_TERMS)
    return False


class DataWorkflowMiddleware(AgentMiddleware):
    """把 DataAgent 的关键前置条件放到运行时，而不是只交给提示词。"""

    def _workflow(self, context: Any) -> dict[str, bool]:
        workflow = getattr(context, "_data_workflow", None)
        if not isinstance(workflow, dict):
            workflow = {}
            setattr(context, "_data_workflow", workflow)
        return workflow

    @staticmethod
    def _blocked(tool_call_id: str, message: str) -> ToolMessage:
        return ToolMessage(
            content=json.dumps({"ok": False, "error": message}, ensure_ascii=False),
            tool_call_id=tool_call_id,
            status="error",
        )

    async def awrap_tool_call(
        self,
        request,
        handler: Callable[[Any], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_name = str((request.tool_call or {}).get("name") or "")
        tool_call_id = str((request.tool_call or {}).get("id") or "data-workflow")
        workflow = self._workflow(request.runtime.context)

        if tool_name in {"omd_get_table_schema", "sql_validate", "sql_execute_query"}:
            if _requires_rag(getattr(request, "state", {})) and not workflow.get("rag_completed"):
                return self._blocked(
                    tool_call_id,
                    "当前问题涉及业务指标口径，必须先调用 rag_search 获取口径依据。",
                )
        if tool_name == "sql_execute_query":
            if not workflow.get("schema_confirmed"):
                return self._blocked(
                    tool_call_id,
                    "执行 SQL 前必须先调用 omd_get_table_schema 确认真实表结构。",
                )
            if not workflow.get("sql_validated"):
                return self._blocked(
                    tool_call_id,
                    "执行 SQL 前必须先调用 sql_validate 完成只读校验。",
                )

        result = await handler(request)
        if not isinstance(result, ToolMessage) or result.status == "error":
            return result

        if tool_name == "rag_search":
            workflow["rag_completed"] = True
        elif tool_name == "omd_get_table_schema":
            workflow["schema_confirmed"] = True
        elif tool_name == "sql_validate":
            workflow["sql_validated"] = True
        elif tool_name == "sql_execute_query":
            workflow["sql_executed"] = True

        return Command(update={"messages": [result], "data_workflow": dict(workflow)})
