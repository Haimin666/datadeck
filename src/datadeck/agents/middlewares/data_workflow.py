"""DataAgent 的取数流程门控：元数据确认和 SQL 自检必须先于执行。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

_RAG_TERMS = ("口径", "定义", "公式", "指标", "逾期率", "怎么算", "含义")
_DATA_TOOLS = {"metric_lookup", "rag_search", "omd_list_services", "omd_list_databases", "omd_search_tables",
               "omd_list_tables", "omd_get_table_schema", "omd_get_table_lineage",
               "sql_validate", "sql_execute_query"}
_OMD_CONTEXT_TOOLS = {"omd_list_tables", "omd_get_table_schema", "omd_get_table_lineage"}


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

    def _workflow(self, context: Any) -> dict[str, Any]:
        workflow = getattr(context, "_data_workflow", None)
        if not isinstance(workflow, dict):
            workflow = {
                "intent": str(getattr(context, "task_kind", "") or "unknown"),
                "phase": "intent",
                "evidence": [],
                "steps": [],
            }
            setattr(context, "_data_workflow", workflow)
        return workflow

    @staticmethod
    def _record(workflow: dict[str, Any], tool_name: str) -> None:
        steps = workflow.setdefault("steps", [])
        if tool_name not in steps:
            steps.append(tool_name)
        phase = {
            "metric_lookup": "evidence",
            "rag_search": "evidence",
            "omd_search_tables": "metadata_search",
            "omd_list_services": "metadata",
            "omd_list_databases": "metadata",
            "omd_list_tables": "metadata",
            "omd_get_table_schema": "schema_confirmed",
            "omd_get_table_lineage": "lineage",
            "sql_validate": "sql_validated",
            "sql_execute_query": "result",
        }.get(tool_name)
        if phase:
            workflow["phase"] = phase

    @staticmethod
    def _blocked(tool_call_id: str, message: str) -> ToolMessage:
        return ToolMessage(
            content=json.dumps({"ok": False, "error": message}, ensure_ascii=False),
            tool_call_id=tool_call_id,
            status="error",
        )

    @staticmethod
    def _tool_payload(result: Any) -> dict[str, Any]:
        """解析工具返回的 JSON，解析失败时返回空对象。"""
        content = result.content if isinstance(result, ToolMessage) else result
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                value = json.loads(content)
            except (TypeError, ValueError):
                return {}
            return value if isinstance(value, dict) else {}
        return {}

    async def awrap_tool_call(
        self,
        request,
        handler: Callable[[Any], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_name = str((request.tool_call or {}).get("name") or "")
        tool_call_id = str((request.tool_call or {}).get("id") or "data-workflow")
        workflow = self._workflow(request.runtime.context)
        intent = workflow.get("intent")

        if intent == "sync" and tool_name in _DATA_TOOLS:
            return self._blocked(
                tool_call_id,
                "同步任务必须直接使用已授权的 dba Skill；禁止调用无关的 OMD、RAG 或 SQL 工具。",
            )

        if tool_name in {"omd_get_table_schema", "sql_validate", "sql_execute_query"}:
            if _requires_rag(getattr(request, "state", {})) and not workflow.get("rag_completed"):
                return self._blocked(
                    tool_call_id,
                    "当前问题涉及业务指标口径，必须先调用 rag_search 获取口径依据。",
                )
        args = (request.tool_call or {}).get("args") or {}
        if tool_name in _OMD_CONTEXT_TOOLS:
            service_name = str(args.get("service_name") or "").strip()
            if not service_name:
                if workflow.get("omd_search_completed"):
                    message = (
                        "OMD 已完成表搜索，但后续查询仍必须使用命中候选的 service_name；"
                        "请从搜索结果复制唯一候选的 Service 后重试。"
                    )
                else:
                    message = "未提供 OMD Service，必须先调用 omd_search_tables 搜索全部可见 Service。"
                return self._blocked(
                    tool_call_id,
                    message,
                )
        if workflow.get("omd_selection_required") and tool_name not in {
            "omd_search_tables", "ask_user_question",
        }:
            return self._blocked(
                tool_call_id,
                "OMD 搜索命中多个候选，必须先调用 ask_user_question 让用户选择，不能猜测表。",
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

        self._record(workflow, tool_name)
        if tool_name == "metric_lookup":
            workflow.setdefault("evidence", []).append("ossie")
        if tool_name == "rag_search":
            workflow["rag_completed"] = True
            workflow.setdefault("evidence", []).append("rag")
        elif tool_name == "omd_get_table_schema":
            workflow["schema_confirmed"] = True
            workflow.setdefault("evidence", []).append("omd_schema")
        elif tool_name.startswith("omd_"):
            workflow.setdefault("evidence", []).append("omd")
            if tool_name == "omd_search_tables":
                payload = self._tool_payload(result)
                candidates = payload.get("candidates") or []
                workflow["omd_search_completed"] = True
                workflow["omd_candidate_count"] = len(candidates) if isinstance(candidates, list) else 0
                workflow["omd_selection_required"] = (
                    not isinstance(candidates, list) or len(candidates) != 1
                )
        elif tool_name == "ask_user_question":
            workflow["omd_selection_required"] = False
            workflow["omd_user_clarified"] = True
        elif tool_name == "sql_validate":
            workflow["sql_validated"] = True
        elif tool_name == "sql_execute_query":
            workflow["sql_executed"] = True

        return Command(update={"messages": [result], "data_workflow": dict(workflow)})
