from datetime import datetime

import pytest

from datadeck.agents.buildin.chatbot.context import ChatBotContext
from datadeck.agents.buildin.chatbot.graph import ChatbotAgent
from datadeck.agents.buildin.dataagent import DATA_AGENT_TOOLS, DataAgent
from server.services.knowledge_service import _chunks
from server.utils.cron import cron_matches, validate_cron_expression
from server.routers.eval_router import _extract_stream_event
from datadeck.agents.middlewares.data_workflow import DataWorkflowMiddleware
from langchain_core.messages import ToolMessage
from datadeck.agents.tool_approval import SENSITIVE_BACKEND_TOOLS
from datadeck.agents.toolkits.service import get_tool_metadata
from server.services.agent_runtime_tools import build_agent_runtime_tools, summarize_subagent_results
from server.services.mcp.service import _mcp_discovery_timeout


@pytest.mark.asyncio
async def test_dataagent_has_data_defaults_without_changing_generic_agent(monkeypatch):
    async def fake_get_graph(self, context=None, **kwargs):
        return context

    monkeypatch.setattr(ChatbotAgent, "get_graph", fake_get_graph)
    context = ChatBotContext()
    result = await DataAgent.get_graph(object.__new__(DataAgent), context)

    assert result.tools == DATA_AGENT_TOOLS
    assert result.sql_guard_enabled is True


@pytest.mark.asyncio
async def test_dataagent_keeps_data_tools_when_host_configures_extra_tools(monkeypatch):
    async def fake_get_graph(self, context=None, **kwargs):
        return context

    monkeypatch.setattr(ChatbotAgent, "get_graph", fake_get_graph)
    context = ChatBotContext(tools=["echo"])
    result = await DataAgent.get_graph(object.__new__(DataAgent), context)

    assert result.tools[:len(DATA_AGENT_TOOLS)] == DATA_AGENT_TOOLS
    assert "echo" in result.tools


def test_text_knowledge_chunks_are_bounded_and_overlap():
    chunks = _chunks("第一段内容。\n\n第二段内容。", chunk_size=8, overlap=2)

    assert chunks
    assert all(len(item) <= 8 for item in chunks)
    assert "第一段内容" in chunks[0]


def test_scheduled_task_mutations_require_approval_by_default():
    assert {
        "scheduled_task_create",
        "scheduled_task_update",
        "scheduled_task_delete",
        "subagent_start",
        "subagent_orchestrate",
        "subagent_cancel",
    } <= SENSITIVE_BACKEND_TOOLS


def test_platform_tools_are_visible_in_tool_metadata():
    slugs = {item["slug"] for item in get_tool_metadata()}
    assert {"read_file", "scheduled_task_list", "scheduled_task_create"} <= slugs
    assert {"subagent_start", "subagent_status", "subagent_await"} <= slugs
    assert "subagent_orchestrate" in slugs


def test_mcp_discovery_timeout_is_bounded():
    assert _mcp_discovery_timeout({"timeout": 10}) == 10
    assert _mcp_discovery_timeout({"timeout": 0}) == 30
    assert _mcp_discovery_timeout({"timeout": 999}) == 120


def test_context_exposes_subagent_allowlist():
    context = ChatBotContext(subagents=["researcher"])
    assert context.subagents == ["researcher"]


def test_runtime_tool_contract_includes_subagent_lifecycle_tools():
    context = ChatBotContext(thread_id="parent", run_id="run", subagents=["researcher"])
    user = type("User", (), {"uid": "u1"})()
    tools = build_agent_runtime_tools(context, user)
    names = {tool.name for tool in tools}
    assert {"subagent_start", "subagent_status", "subagent_events",
            "subagent_cancel", "subagent_await", "subagent_orchestrate"} <= names
    assert all(tool.handle_tool_error is True for tool in tools)


def test_subagent_summary_rejects_partial_failure():
    result = summarize_subagent_results({"a": {"status": "completed"}, "b": {"status": "failed"}})
    assert result == {"status": "failed", "completed": 1, "failed": 1, "total": 2}


def test_cron_validation_is_shared_by_api_and_scheduler():
    assert validate_cron_expression("*/10 9-17 * * 1-5") == "*/10 9-17 * * 1-5"
    assert cron_matches("0 9 * * 1-5", datetime(2026, 9, 11, 9, 0))
    for expression in ("60 * * * *", "0 25 * * *", "0 9 32 * *", "0 9 * 13 *"):
        with pytest.raises(ValueError):
            validate_cron_expression(expression)


@pytest.mark.asyncio
async def test_data_workflow_blocks_sql_until_schema_and_validation():
    middleware = DataWorkflowMiddleware()
    context = type("Context", (), {})()
    runtime = type("Runtime", (), {"context": context})()
    request = type("Request", (), {
        "runtime": runtime,
        "tool_call": {"id": "call-1", "name": "sql_execute_query", "args": {}},
    })()

    blocked = await middleware.awrap_tool_call(request, lambda _request: None)
    assert blocked.status == "error"

    context._data_workflow = {"schema_confirmed": True, "sql_validated": True}

    async def handler(_request):
        return ToolMessage(content="ok", tool_call_id="call-1")

    result = await middleware.awrap_tool_call(request, handler)
    assert result.update["data_workflow"]["sql_executed"] is True


@pytest.mark.asyncio
async def test_data_workflow_requires_rag_for_metric_queries():
    middleware = DataWorkflowMiddleware()
    context = type("Context", (), {})()
    runtime = type("Runtime", (), {"context": context})()
    request = type("Request", (), {
        "runtime": runtime,
        "state": {"messages": [type("Message", (), {"type": "human", "content": "逾期率怎么算"})()]},
        "tool_call": {"id": "call-2", "name": "omd_get_table_schema", "args": {}},
    })()

    blocked = await middleware.awrap_tool_call(request, lambda _request: None)
    assert blocked.status == "error"
    assert "rag_search" in blocked.content


def test_evaluation_parser_matches_current_nested_sse_contract():
    assert _extract_stream_event({
        "event": "messages",
        "payload": {"chunk": {"stream_event": {
            "type": "message_delta", "content": "结果",
        }}},
    }) == ("message_delta", "结果")
    assert _extract_stream_event({
        "event": "messages",
        "payload": {"chunk": {"stream_event": {
            "type": "tool_call", "name": "rag_search",
        }}},
    }) == ("tool_call", "rag_search")
