from datetime import datetime
import json

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
from datadeck.agents.policy import DATA_AGENT_POLICY
from datadeck.agents.toolkits.service import get_tool_descriptors
from datadeck.agents.toolkits.service import get_tool_metadata
from datadeck.agents.toolkits.service import get_tool_instances_for_context
from server.services.agent_runtime_tools import (
    _selected_runtime_ids,
    build_agent_runtime_tools,
    summarize_subagent_results,
)
from server.services.mcp.service import _mcp_discovery_timeout, _mcp_error_summary
import server.services.mcp.service as mcp_service


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
    context = ChatBotContext(tools=["package:platform"])
    result = await DataAgent.get_graph(object.__new__(DataAgent), context)

    assert result.tools[:len(DATA_AGENT_TOOLS)] == DATA_AGENT_TOOLS
    assert "package:platform" in result.tools


@pytest.mark.asyncio
async def test_dataagent_metadata_graph_is_available_for_history(monkeypatch):
    captured = {}

    async def fake_parent_graph(self, context=None, **kwargs):
        captured["context"] = context
        captured["kwargs"] = kwargs
        return context

    monkeypatch.setattr(ChatbotAgent, "get_graph", fake_parent_graph)

    result = await DataAgent.get_graph(
        object.__new__(DataAgent), metadata_only=True,
    )

    assert result._runtime_mode == "metadata"
    assert result.tools[:len(DATA_AGENT_TOOLS)] == DATA_AGENT_TOOLS
    assert captured["kwargs"]["metadata_only"] is True


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
    assert {"read_file", "ask_user_question", "scheduled_task_list", "scheduled_task_create"} <= slugs
    assert {"subagent_start", "subagent_status", "subagent_await"} <= slugs
    assert "subagent_orchestrate" in slugs


def test_dataagent_fixed_tools_are_visible_to_runtime_assembler():
    slugs = {item.slug for item in get_tool_descriptors()}

    assert set(DATA_AGENT_POLICY.fixed_tool_selection) <= slugs


def test_metric_review_status_is_normalized_to_persisted_status():
    from server.routers.metric_router import _normalize_metric_context

    assert _normalize_metric_context(
        {"source": "code", "review_status": "needs_review"}, "approved",
    ) == {"source": "code", "review_status": "approved"}


def test_mcp_discovery_timeout_is_bounded():
    assert _mcp_discovery_timeout({"timeout": 10}) == 10
    assert _mcp_discovery_timeout({"timeout": 0}) == 30
    assert _mcp_discovery_timeout({"timeout": 999}) == 120


def test_context_exposes_subagent_allowlist():
    context = ChatBotContext(subagents=["researcher"])
    assert context.subagents == ["researcher"]


def test_runtime_tool_contract_includes_subagent_lifecycle_tools():
    context = ChatBotContext(
        thread_id="parent", run_id="run", subagents=["researcher"], delegation_enabled=True,
    )
    user = type("User", (), {"uid": "u1"})()
    tools = build_agent_runtime_tools(context, user)
    names = {tool.name for tool in tools}
    assert {"subagent_start", "subagent_status", "subagent_events",
            "subagent_cancel", "subagent_await", "subagent_orchestrate"} <= names
    assert all(tool.handle_tool_error is True for tool in tools)


def test_scheduled_task_tools_use_the_runtime_resource_selection():
    selected = ChatBotContext(scheduled_tasks=["task-1", "task-2"])
    assert _selected_runtime_ids(selected, "scheduled_tasks") == {"task-1", "task-2"}

    selected.scheduled_tasks = []
    assert _selected_runtime_ids(selected, "scheduled_tasks") == set()

    default = ChatBotContext(scheduled_tasks=None)
    assert _selected_runtime_ids(default, "scheduled_tasks") is None


def test_subagent_tools_require_explicit_delegation_and_allowlist():
    user = type("User", (), {"uid": "u1"})()
    disabled = build_agent_runtime_tools(
        ChatBotContext(thread_id="parent", run_id="run", subagents=["researcher"]), user,
    )
    assert "subagent_start" not in {tool.name for tool in disabled}

    empty_allowlist = build_agent_runtime_tools(
        ChatBotContext(thread_id="parent", run_id="run", subagents=[], delegation_enabled=True), user,
    )
    assert "subagent_start" not in {tool.name for tool in empty_allowlist}


def test_dataagent_policy_blocks_subagent_tools_even_if_configured():
    user = type("User", (), {"uid": "u1"})()
    context = ChatBotContext(
        tools=DATA_AGENT_TOOLS, subagents=["researcher"], delegation_enabled=True,
    )
    context.agent_backend_id = "DataAgent"

    names = {tool.name for tool in build_agent_runtime_tools(context, user)}

    assert "metric_lookup" in names
    assert "subagent_start" not in names


def test_dataagent_code_search_is_only_mounted_for_code_logic_questions():
    user = type("User", (), {"uid": "u1"})()
    normal = ChatBotContext(tools=DATA_AGENT_TOOLS, agent_backend_id="DataAgent", task_kind="schema")
    code = ChatBotContext(tools=DATA_AGENT_TOOLS, agent_backend_id="DataAgent", task_kind="code")

    normal_names = {tool.name for tool in build_agent_runtime_tools(normal, user)}
    code_names = {tool.name for tool in build_agent_runtime_tools(code, user)}

    assert "code_search" not in normal_names
    assert "code_search" in code_names


def test_pure_chat_does_not_mount_host_tools():
    user = type("User", (), {"uid": "u1"})()
    context = ChatBotContext(tools=["package:platform"], task_kind="chat")

    assert build_agent_runtime_tools(context, user) == []
    assert get_tool_instances_for_context(context) == []


def test_knowledge_package_mounts_metric_lookup_for_generic_agent():
    user = type("User", (), {"uid": "u1"})()
    context = ChatBotContext(
        tools=["package:knowledge"],
        agent_backend_id="ChatbotAgent",
    )

    names = {
        tool.name for tool in get_tool_instances_for_context(context)
    }
    names.update(tool.name for tool in build_agent_runtime_tools(context, user))

    assert {"rag_search", "metric_lookup"} <= names


def test_subagent_summary_rejects_partial_failure():
    result = summarize_subagent_results({"a": {"status": "completed"}, "b": {"status": "failed"}})
    assert result == {"status": "failed", "completed": 1, "failed": 1, "total": 2}


def test_cron_validation_is_shared_by_api_and_scheduler():
    assert validate_cron_expression("*/10 9-17 * * 1-5") == "*/10 9-17 * * 1-5"
    assert cron_matches("0 9 * * 1-5", datetime(2026, 9, 11, 9, 0))
    for expression in ("60 * * * *", "0 25 * * *", "0 9 32 * *", "0 9 * 13 *"):
        with pytest.raises(ValueError):
            validate_cron_expression(expression)


def test_mcp_error_summary_keeps_nested_connection_reason_and_redacts_secrets():
    error = ExceptionGroup("TaskGroup", [
        ConnectionError("cannot connect to mcp.internal:3000"),
        ValueError("Authorization: Bearer top-secret"),
        OSError("request failed for https://mcp.internal:3000/sse?token=path-secret"),
    ])

    summary = _mcp_error_summary(error)

    assert "ConnectionError" in summary
    assert "cannot connect to mcp.internal:3000" in summary
    assert "top-secret" not in summary
    assert "path-secret" not in summary
    assert "https://mcp.internal:3000/<redacted>" in summary
    assert "Authorization=<redacted>" in summary


@pytest.mark.asyncio
async def test_mcp_http_client_does_not_inherit_ambient_proxy(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, configs):
            captured.update(configs)

    monkeypatch.setattr(mcp_service, "MultiServerMCPClient", FakeClient)
    monkeypatch.delenv("DATADECK_MCP_HTTP_PROXY", raising=False)
    await mcp_service.get_mcp_client({
        "http": {"transport": "streamable_http", "url": "http://example.test/mcp"},
        "stdio": {"transport": "stdio", "command": "python", "args": []},
    })

    factory = captured["http"]["httpx_client_factory"]
    client = factory(headers=None, timeout=None, auth=None)
    try:
        assert client._trust_env is False
    finally:
        await client.aclose()
    assert "httpx_client_factory" not in captured["stdio"]


@pytest.mark.asyncio
async def test_remote_model_catalog_does_not_inherit_ambient_proxy(monkeypatch):
    from types import SimpleNamespace
    import server.services.model_providers.service as provider_service

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    async def fetch(_client, _provider, _headers, _endpoint, _model_type):
        return []

    monkeypatch.setattr(provider_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(provider_service, "_fetch_models_from_endpoint", fetch)

    await provider_service.fetch_remote_models(SimpleNamespace(
        base_url="https://example.test/v1", proxy_url="", api_key="",
        api_key_env="", headers_json={}, capabilities=[],
        models_endpoint="/models", embedding_models_endpoint=None,
        rerank_models_endpoint=None,
    ))

    assert captured["kwargs"]["trust_env"] is False


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


@pytest.mark.asyncio
async def test_data_workflow_routes_sync_to_dba_without_omd_fallback():
    middleware = DataWorkflowMiddleware()
    context = type("Context", (), {"task_kind": "sync"})()
    runtime = type("Runtime", (), {"context": context})()
    request = type("Request", (), {
        "runtime": runtime,
        "tool_call": {"id": "call-sync", "name": "omd_search_tables", "args": {}},
    })()

    blocked = await middleware.awrap_tool_call(request, lambda _request: None)

    assert blocked.status == "error"
    assert "dba Skill" in blocked.content
    assert context._data_workflow["intent"] == "sync"
    assert context._data_workflow["steps"] == []


@pytest.mark.asyncio
async def test_data_workflow_persists_steps_and_evidence_after_tool_success():
    middleware = DataWorkflowMiddleware()
    context = type("Context", (), {"task_kind": "data"})()
    runtime = type("Runtime", (), {"context": context})()
    request = type("Request", (), {
        "runtime": runtime,
        "tool_call": {"id": "call-schema", "name": "omd_get_table_schema",
                       "args": {"service_name": "warehouse"}},
    })()

    async def handler(_request):
        return ToolMessage(content="schema", tool_call_id="call-schema")

    result = await middleware.awrap_tool_call(request, handler)

    assert result.update["data_workflow"]["steps"] == ["omd_get_table_schema"]
    assert result.update["data_workflow"]["evidence"] == ["omd_schema"]
    assert result.update["data_workflow"]["phase"] == "schema_confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("candidates, expected", [([], True), ([{"table": "one"}], False)])
async def test_data_workflow_marks_omd_search_selection_for_zero_or_single_candidate(
    candidates, expected,
):
    middleware = DataWorkflowMiddleware()
    context = type("Context", (), {"task_kind": "schema"})()
    runtime = type("Runtime", (), {"context": context})()
    request = type("Request", (), {
        "runtime": runtime,
        "tool_call": {"id": "call-search", "name": "omd_search_tables", "args": {}},
    })()

    async def handler(_request):
        return ToolMessage(
            content=json.dumps({"ok": True, "candidates": candidates}),
            tool_call_id="call-search",
        )

    result = await middleware.awrap_tool_call(request, handler)

    assert result.update["data_workflow"]["omd_selection_required"] is expected


@pytest.mark.asyncio
async def test_data_workflow_blocks_omd_after_zero_candidate_search():
    middleware = DataWorkflowMiddleware()
    context = type("Context", (), {"task_kind": "schema"})()
    runtime = type("Runtime", (), {"context": context})()
    search_request = type("Request", (), {
        "runtime": runtime,
        "tool_call": {"id": "call-search", "name": "omd_search_tables", "args": {}},
    })()

    async def search_handler(_request):
        return ToolMessage(
            content=json.dumps({"ok": True, "candidates": []}),
            tool_call_id="call-search",
        )

    await middleware.awrap_tool_call(search_request, search_handler)
    schema_request = type("Request", (), {
        "runtime": runtime,
        "tool_call": {"id": "call-schema", "name": "omd_get_table_schema",
                       "args": {"service_name": "warehouse"}},
    })()

    blocked = await middleware.awrap_tool_call(schema_request, lambda _request: None)

    assert blocked.status == "error"
    assert "ask_user_question" in blocked.content


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
