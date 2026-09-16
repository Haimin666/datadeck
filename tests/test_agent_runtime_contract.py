from __future__ import annotations

import pytest
from types import SimpleNamespace

from server.services.agent_runtime_assembler import AgentRuntimeAssembler
from server.services.agent_runtime_contract import (
    RUNTIME_SNAPSHOT_VERSION,
    RuntimeAssemblyError,
    RuntimeResource,
    RuntimeResourceSnapshot,
)
from server.services.agent_runtime_tools import (
    build_agent_runtime_tools,
    tool_allowed_by_runtime_permissions,
)


def make_snapshot(**kwargs):
    uid = kwargs.pop("uid", "user-1")
    thread_id = kwargs.pop("thread_id", "thread-1")
    return RuntimeResourceSnapshot(
        uid=uid,
        agent_slug="default-chatbot",
        thread_id=thread_id,
        **kwargs,
    )


def test_none_and_empty_selection_have_different_meanings():
    snapshot = make_snapshot(selections={"tools": None, "skills": ()})

    assert snapshot.selected("tools") is None
    assert snapshot.selected("skills") == ()
    assert snapshot.to_dict()["selections"] == {"tools": None, "skills": []}


def test_runtime_snapshot_captures_identity_permissions_and_packages():
    snapshot = make_snapshot(
        agent_backend_id="ChatbotAgent",
        permissions=("knowledge", "agents", "knowledge"),
        package_selections={"tool_packages": ("package:platform",)},
    )

    payload = snapshot.to_dict()
    assert payload["schema_version"] == RUNTIME_SNAPSHOT_VERSION
    assert payload["agent_backend_id"] == "ChatbotAgent"
    assert payload["permissions"] == ["agents", "knowledge"]
    assert payload["package_selections"] == {"tool_packages": ["package:platform"]}

    with pytest.raises(AttributeError):
        snapshot.permissions.append("users")


def test_runtime_permissions_gate_tools_server_side():
    context = SimpleNamespace(_runtime_snapshot=make_snapshot(
        permissions=("conversations", "knowledge"),
    ))

    assert tool_allowed_by_runtime_permissions(context, "rag_search") is True
    assert tool_allowed_by_runtime_permissions(context, "scheduled_task_list") is False
    assert tool_allowed_by_runtime_permissions(context, "read_file") is False
    assert tool_allowed_by_runtime_permissions(context, "ask_user_question") is True


@pytest.mark.asyncio
async def test_skill_runtime_does_not_query_without_extensions_permission(monkeypatch):
    import server.services.skills.runtime as runtime

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("没有 extensions 权限时不应查询 Skill")

    monkeypatch.setattr(runtime, "list_accessible_skills", fail_if_called)
    result = await runtime.resolve_runtime_skills_for_context(
        SimpleNamespace(
            runtime_permissions=("conversations",), skills=None, preload_skills=None,
        ),
        db=object(), user=object(),
    )

    assert result["context_skills"] == []
    assert result["runtime_skills"] == {}


@pytest.mark.asyncio
async def test_runtime_assembler_does_not_mount_forbidden_modules(monkeypatch):
    import server.services.agent_runtime_assembler as assembler_module

    async def permissions(*_args, **_kwargs):
        return ["conversations"]

    monkeypatch.setattr(assembler_module, "get_role_permissions", permissions)
    monkeypatch.setattr(assembler_module, "get_tool_descriptors", lambda: [])
    monkeypatch.setattr(assembler_module, "get_tool_instances_for_context", lambda _context: [])

    context = SimpleNamespace(
        thread_id="thread-1", uid="user-1", tools=[], skills=None,
        knowledges=None, subagents=None, scheduled_tasks=None,
        workdir=object(), workdir_path="/projects/demo",
    )
    user = SimpleNamespace(uid="user-1", role="limited")
    snapshot = await AgentRuntimeAssembler().assemble(
        context, db=object(), user=user, agent_slug="default-chatbot",
    )

    assert snapshot.permissions == ("conversations",)
    assert snapshot.workdir_path is None
    assert context.workdir is None
    assert {item.kind for item in snapshot.resources} == {"workspace"}


def test_resource_snapshot_is_serializable_and_filters_mounted_resources():
    snapshot = make_snapshot(resources=(
        RuntimeResource(kind="tools", key="rag_search", source="builtin"),
        RuntimeResource(kind="mcps", key="disabled-mcp", status="unavailable", reason="disabled"),
    ))

    assert [item.key for item in snapshot.mounted_resources()] == ["rag_search"]
    assert snapshot.mounted_resources("tools")[0].source == "builtin"
    assert snapshot.to_dict()["resources"][1]["reason"] == "disabled"


@pytest.mark.asyncio
async def test_mcp_discovery_diagnostic_redacts_url_credentials(monkeypatch):
    import server.services.mcp.service as mcp_service

    async def fail(_slug):
        raise OSError("request failed for https://mcp.internal:3000/sse?token=secret-token")

    monkeypatch.setattr(mcp_service, "get_mcp_tools", fail, raising=False)
    context = SimpleNamespace(
        _runtime_snapshot=make_snapshot(
            selections={"mcps": ("analytics",)},
            resources=(RuntimeResource(kind="mcps", key="analytics", source="postgres"),),
        ),
        _runtime_diagnostics=[],
    )

    result = await AgentRuntimeAssembler.resolve_mcp_tools_by_server(
        context, extra_mcps=["analytics"],
    )

    assert result == {"analytics": []}
    message = context._runtime_diagnostics[0]["message"]
    assert "secret-token" not in message
    assert "https://mcp.internal:3000/<redacted>" in message


@pytest.mark.asyncio
async def test_mcp_discovery_mounts_only_snapshot_selected_servers(monkeypatch):
    import server.services.mcp.service as mcp_service

    async def discover(server_slug):
        return [SimpleNamespace(name=f"{server_slug}_tool")]

    monkeypatch.setattr(mcp_service, "get_mcp_tools", discover, raising=False)
    context = SimpleNamespace(
        _runtime_snapshot=make_snapshot(
            selections={"mcps": ("analytics",)},
            resources=(
                RuntimeResource(kind="mcps", key="analytics", source="postgres"),
                RuntimeResource(kind="mcps", key="other", source="postgres"),
            ),
        ),
        _runtime_diagnostics=[],
    )

    result = await AgentRuntimeAssembler.resolve_mcp_tools_by_server(
        context, extra_mcps=["analytics", "other", "unmounted"],
    )

    assert list(result) == ["analytics"]
    assert result["analytics"][0].name == "analytics_tool"
    assert context._runtime_diagnostics == []


def test_runtime_diagnostic_and_tool_descriptor_are_serializable():
    from server.services.agent_runtime_contract import RuntimeDiagnostic, ToolDescriptor

    diagnostic = RuntimeDiagnostic(
        code="MCP_DISCOVERY_FAILED", message="连接超时", resource_kind="mcps",
        resource_key="analytics", details={"timeout": 30},
    )
    descriptor = ToolDescriptor(
        slug="sql_execute_query", name="SQL 查询", group="capability",
        package_slug="package:sql", tags=("SQL",), input_schema={"type": "object"},
    )

    assert diagnostic.to_dict()["recoverable"] is True
    assert descriptor.to_dict()["package_slug"] == "package:sql"
    assert descriptor.to_dict()["tags"] == ["SQL"]


@pytest.mark.parametrize("value, expected", [
    (None, None),
    ([], ()),
    ([" tools ", ""], ("tools",)),
])
def test_normalize_selection(value, expected):
    assert RuntimeResourceSnapshot.normalize_selection(value) == expected


def test_invalid_resource_is_rejected():
    with pytest.raises(ValueError, match="kind"):
        RuntimeResource(kind="", key="tool")

    with pytest.raises(ValueError, match="selection"):
        RuntimeResourceSnapshot.normalize_selection("tools")
    with pytest.raises(ValueError, match="items"):
        RuntimeResourceSnapshot.normalize_selection(["tools", 1])


def test_invalid_runtime_selection_is_structured():
    from server.services.agent_runtime_assembler import _selected

    with pytest.raises(RuntimeAssemblyError) as error:
        _selected("tools")
    assert error.value.diagnostic.code == "RUNTIME_SELECTION_INVALID"


def test_default_tool_snapshot_marks_only_default_tools_as_mounted():
    from server.services.agent_runtime_assembler import _with_selection_status

    available = [
        RuntimeResource(kind="tools", key="echo", source="builtin"),
        RuntimeResource(kind="tools", key="package:sql", source="builtin"),
    ]

    result = _with_selection_status(
        available, None, kind="tools", default_keys={"echo"},
    )

    assert [item.key for item in result if item.status == "mounted"] == ["echo"]
    assert result[1].status == "skipped"
    assert result[1].reason == "默认策略未挂载"


def test_default_policy_skips_do_not_become_runtime_error_diagnostics():
    from server.services.agent_runtime_assembler import _resource_diagnostic

    skipped = RuntimeResource(
        kind="tools", key="package:sql", status="skipped", reason="默认策略未挂载",
    )
    unavailable = RuntimeResource(
        kind="tools", key="package:mcp:missing", status="unavailable", reason="MCP 未发现",
    )

    assert _resource_diagnostic(skipped) is None
    diagnostic = _resource_diagnostic(unavailable)
    assert diagnostic is not None
    assert diagnostic.code == "RUNTIME_RESOURCE_UNAVAILABLE"
    assert diagnostic.message == "MCP 未发现"


def test_scheduled_task_snapshot_honors_explicit_selection():
    from server.services.agent_runtime_assembler import _with_selection_status

    available = [
        RuntimeResource(kind="scheduled_tasks", key="task-a", name="A"),
        RuntimeResource(kind="scheduled_tasks", key="task-b", name="B"),
    ]

    assert _with_selection_status(
        available, (), kind="scheduled_tasks",
    ) == []

    selected = _with_selection_status(
        available, ("task-b", "missing"), kind="scheduled_tasks",
    )
    assert [(item.key, item.status) for item in selected] == [
        ("task-b", "mounted"),
        ("missing", "unavailable"),
    ]


def test_knowledge_context_preserves_explicit_empty_selection():
    context = SimpleNamespace(knowledge_base_id="old", knowledge_base_collection="old-collection")
    snapshot = make_snapshot(
        selections={"knowledges": ()},
        resources=(RuntimeResource(
            kind="knowledges", key="kb-1", name="KB", source="postgres",
            metadata={"collection_name": "collection-1"},
        ),),
    )

    AgentRuntimeAssembler.apply_knowledge_context(context, snapshot)

    assert context.knowledge_base_collections == []
    assert context.knowledge_base_id is None
    assert context.knowledge_base_collection is None


def test_knowledge_context_keeps_all_authorized_collections():
    context = SimpleNamespace()
    snapshot = make_snapshot(
        selections={"knowledges": ("kb-1", "kb-2")},
        resources=(
            RuntimeResource(kind="knowledges", key="kb-1", name="KB 1", source="postgres",
                            metadata={"collection_name": "collection-1"}),
            RuntimeResource(kind="knowledges", key="kb-2", name="KB 2", source="postgres",
                            metadata={"collection_name": "collection-2"}),
        ),
    )

    AgentRuntimeAssembler.apply_knowledge_context(context, snapshot)

    assert context.knowledge_base_collections == ["collection-1", "collection-2"]
    assert context.knowledge_base_collection == "collection-1"


def test_knowledge_context_limits_search_to_explicit_selection():
    context = SimpleNamespace()
    snapshot = make_snapshot(
        selections={"knowledges": ("kb-2",)},
        resources=(
            RuntimeResource(kind="knowledges", key="kb-1", name="KB 1", source="postgres",
                            metadata={"collection_name": "collection-1"}),
            RuntimeResource(kind="knowledges", key="kb-2", name="KB 2", source="postgres",
                            metadata={"collection_name": "collection-2"}),
        ),
    )

    AgentRuntimeAssembler.apply_knowledge_context(context, snapshot)

    assert context.knowledge_base_collections == ["collection-2"]
    assert context.knowledge_base_collection == "collection-2"


@pytest.mark.asyncio
async def test_prepare_context_preserves_preparation_diagnostics(monkeypatch):
    """快照装配前的附件/资源诊断必须进入同一次运行的 Trace。"""
    import server.deps as deps
    import server.services.agent_runtime_middlewares as middleware_module
    import server.services.agent_runtime_tools as runtime_tools_module
    import server.services.agent_runtime_assembler as assembler_module
    import server.services.skills.runtime as skill_runtime

    context = SimpleNamespace(
        uid="user-1", thread_id="thread-1", run_id="run-1", agent_backend_id="ChatbotAgent",
        tools=[], skills=[], knowledges=[], subagents=None, scheduled_tasks=None,
        workdir_path="", _runtime_diagnostics=[],
    )
    user = SimpleNamespace(uid="user-1", role="admin")
    snapshot = make_snapshot(uid="user-1", thread_id="thread-1")

    async def allow_agent(*_args, **_kwargs):
        return None

    async def load_attachments(_self, target, **_kwargs):
        target.attachments = []
        target._runtime_diagnostics.append({
            "code": "ATTACHMENT_NOT_FOUND",
            "resource_kind": "attachments",
            "resource_key": "",
            "message": "附件不存在",
        })

    async def assemble(*_args, **_kwargs):
        return snapshot

    async def resolve_skills(*_args, **_kwargs):
        return {
            "context_skills": [], "context_preload_skills": [],
            "effective_skills": [], "runtime_skills": {},
            "preloaded_skills": [], "preloaded_skill_contents": {},
        }

    async def middlewares(*_args, **_kwargs):
        return []

    async def mcp_tools(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(deps, "require_agent_access", allow_agent)
    monkeypatch.setattr(AgentRuntimeAssembler, "_load_attachments", load_attachments)
    monkeypatch.setattr(AgentRuntimeAssembler, "assemble", assemble)
    monkeypatch.setattr(assembler_module, "get_tool_instances_for_context", lambda _context: [])
    monkeypatch.setattr(skill_runtime, "resolve_runtime_skills_for_context", resolve_skills)
    monkeypatch.setattr(runtime_tools_module, "build_agent_runtime_tools", lambda *_args: [])
    monkeypatch.setattr(middleware_module, "build_runtime_middlewares", middlewares)

    runtime = AgentRuntimeAssembler()
    monkeypatch.setattr(runtime, "resolve_mcp_tools_by_server", mcp_tools)

    await runtime.prepare_context(context, db=object(), user=user, agent_slug="default-chatbot")

    assert context._runtime_diagnostics[0]["code"] == "ATTACHMENT_NOT_FOUND"


def test_explicit_tool_selection_also_gates_platform_tools():
    context = SimpleNamespace(
        uid="user-1", thread_id="thread-1", run_id="run-1", subagent_depth=0,
        tools=["scheduled_task_list"], skills=[], agent_backend_id="ChatbotAgent",
        subagents=None, delegation_enabled=False,
    )
    user = SimpleNamespace(uid="user-1")

    tools = build_agent_runtime_tools(context, user)

    assert [item.name for item in tools] == ["scheduled_task_list"]


def test_unconfigured_generic_agent_does_not_expose_platform_mutations():
    context = SimpleNamespace(
        uid="user-1", thread_id="thread-1", run_id="run-1", subagent_depth=0,
        tools=None, skills=[], agent_backend_id="ChatbotAgent",
        subagents=None, delegation_enabled=False, workdir_path="",
    )
    user = SimpleNamespace(uid="user-1")

    assert build_agent_runtime_tools(context, user) == []


def test_platform_package_expands_to_platform_tools():
    from datadeck.agents.toolkits.packages import expand_tool_selection

    expanded = expand_tool_selection(["package:platform"])
    assert "scheduled_task_create" in expanded
    assert "execute" in expanded
    assert "run_skill_script" in expanded
    assert "ask_user_question" in expanded
    assert "present_artifacts" in expanded


def test_skill_selection_preserves_none_and_empty_semantics(monkeypatch):
    import asyncio
    import server.services.skills.runtime as runtime

    class Skill:
        def __init__(self, slug, source_scope):
            self.slug = slug
            self.name = slug
            self.description = ""
            self.source_scope = source_scope
            self.tool_dependencies = []
            self.mcp_dependencies = []
            self.skill_dependencies = []

    async def fake_list(_db, _user):
        return [Skill("builtin-skill", "builtin"), Skill("user-skill", "personal")]

    monkeypatch.setattr(runtime, "list_accessible_skills", fake_list)
    none_result = asyncio.run(runtime.resolve_runtime_skills_for_context(
        SimpleNamespace(skills=None, preload_skills=None), db=object(), user=object()))
    empty_result = asyncio.run(runtime.resolve_runtime_skills_for_context(
        SimpleNamespace(skills=[], preload_skills=None), db=object(), user=object()))

    assert none_result["context_skills"] == ["builtin-skill", "user-skill"]
    assert empty_result["context_skills"] == ["builtin-skill"]


@pytest.mark.asyncio
async def test_graph_cache_is_scoped_to_runtime_identity(monkeypatch):
    import datadeck.agents.buildin.chatbot.graph as graph_module
    from datadeck.agents.buildin.chatbot.context import ChatBotContext
    from datadeck.agents.buildin.chatbot.graph import ChatbotAgent

    created = []
    monkeypatch.setattr(graph_module, "load_chat_model", lambda *args, **kwargs: object())
    monkeypatch.setattr(graph_module, "_build_middlewares", lambda *args, **kwargs: _empty_middlewares())
    monkeypatch.setattr(graph_module, "resolve_configured_runtime_tools", _empty_tools)
    monkeypatch.setattr(graph_module, "create_agent", lambda **kwargs: created.append(object()) or created[-1])

    agent = ChatbotAgent(model_provider=object())
    first_context = ChatBotContext(uid="u1", thread_id="t1", model="test:model")
    first_context._runtime_snapshot = make_snapshot(
        uid="u1", thread_id="t1", diagnostics={"duration_ms": 1}
    )
    first_context._runtime_prepared = True
    second_context = ChatBotContext(uid="u1", thread_id="t1", model="test:model")
    second_context._runtime_snapshot = make_snapshot(
        uid="u1", thread_id="t1", diagnostics={"duration_ms": 999}
    )
    second_context._runtime_prepared = True
    first = await agent.get_graph(context=first_context)
    second = await agent.get_graph(context=second_context)
    other_context = ChatBotContext(uid="u2", thread_id="t1", model="test:model")
    other_context._runtime_prepared = True
    other_user = await agent.get_graph(context=other_context)

    assert first is second
    assert other_user is not first
    assert len(created) == 2


@pytest.mark.asyncio
async def test_formal_runs_do_not_reuse_context_bound_graph(monkeypatch):
    import datadeck.agents.buildin.chatbot.graph as graph_module
    from datadeck.agents.buildin.chatbot.context import ChatBotContext
    from datadeck.agents.buildin.chatbot.graph import ChatbotAgent

    created = []
    monkeypatch.setattr(graph_module, "load_chat_model", lambda *args, **kwargs: object())
    monkeypatch.setattr(graph_module, "_build_middlewares", lambda *args, **kwargs: _empty_middlewares())
    monkeypatch.setattr(graph_module, "resolve_configured_runtime_tools", _empty_tools)
    monkeypatch.setattr(graph_module, "create_agent", lambda **kwargs: created.append(object()) or created[-1])

    agent = ChatbotAgent(model_provider=object())
    for run_id in ("run-1", "run-2"):
        context = ChatBotContext(uid="u1", thread_id="t1", model="test:model")
        context.run_id = run_id
        context._runtime_prepared = True
        await agent.get_graph(context=context)

    assert len(created) == 2


async def _empty_tools(_context):
    return []


async def _empty_middlewares():
    return []


@pytest.mark.asyncio
async def test_core_middlewares_are_not_gated_by_memory_store(monkeypatch):
    """Memory 是可选能力，但基础保护链必须始终存在。"""
    import datadeck.agents.buildin.chatbot.graph as graph_module
    from datadeck.agents.buildin.chatbot.context import ChatBotContext

    monkeypatch.setattr(graph_module, "create_sql_selfcheck_middleware", lambda _context: None)
    monkeypatch.setattr(graph_module, "create_data_selfcheck_middleware", lambda _context: None)
    monkeypatch.setattr(graph_module, "create_memory_middleware", lambda *args, **kwargs: _none_async())
    monkeypatch.setattr(graph_module, "create_tool_approval_middleware", lambda *args, **kwargs: None)
    monkeypatch.setattr(graph_module, "create_summary_middleware", lambda *args, **kwargs: "summary")
    monkeypatch.setattr(graph_module, "TokenBudgetMiddleware", lambda *args, **kwargs: "budget")
    monkeypatch.setattr(graph_module, "TodoListMiddleware", lambda *args, **kwargs: "todo")
    monkeypatch.setattr(graph_module, "PatchToolCallsMiddleware", lambda *args, **kwargs: "patch")
    monkeypatch.setattr(graph_module, "ModelRetryMiddleware", lambda *args, **kwargs: "retry")
    monkeypatch.setattr(graph_module, "TokenUsageMiddleware", lambda *args, **kwargs: "usage")
    monkeypatch.setattr(graph_module, "ToolTimeoutMiddleware", lambda *args, **kwargs: "timeout")

    middlewares = await graph_module._build_middlewares(
        ChatBotContext(), model=object(), memory_store=None,
    )

    assert [type(item).__name__ if not isinstance(item, str) else item for item in middlewares] == [
        "summary", "budget", "todo", "patch", "retry", "usage",
        "ToolFailureGuardMiddleware", "timeout",
        "ToolResultTrustBoundaryMiddleware",
    ]


async def _none_async():
    return None


@pytest.mark.asyncio
async def test_prepared_context_consumes_injected_runtime_middlewares(monkeypatch):
    import datadeck.agents.buildin.chatbot.graph as graph_module
    from datadeck.agents.buildin.chatbot.context import ChatBotContext
    from datadeck.agents.buildin.chatbot.graph import ChatbotAgent

    monkeypatch.setattr(graph_module, "load_chat_model", lambda *args, **kwargs: object())
    monkeypatch.setattr(graph_module, "_build_middlewares", lambda *args, **kwargs: _empty_middlewares())
    monkeypatch.setattr(graph_module, "resolve_configured_runtime_tools", _empty_tools)
    captured = {}
    monkeypatch.setattr(graph_module, "create_agent", lambda **kwargs: captured.update(kwargs) or object())

    context = ChatBotContext(uid="u1", thread_id="t1", model="test:model")
    context._runtime_prepared = True
    middleware = object()
    context.runtime_middlewares = (middleware,)
    agent = ChatbotAgent(model_provider=object())
    await agent.get_graph(context=context)

    assert captured["middleware"] == [middleware]


@pytest.mark.asyncio
async def test_prepared_graph_uses_only_injected_runtime_tools(monkeypatch):
    import datadeck.agents.buildin.chatbot.graph as graph_module
    from datadeck.agents.buildin.chatbot.context import ChatBotContext
    from datadeck.agents.buildin.chatbot.graph import ChatbotAgent

    injected_tool = SimpleNamespace(name="injected_tool")
    captured = {}
    monkeypatch.setattr(graph_module, "load_chat_model", lambda *args, **kwargs: object())
    monkeypatch.setattr(graph_module, "_build_middlewares", lambda *args, **kwargs: _empty_middlewares())

    async def fail_if_resolved(_context):
        raise AssertionError("prepared runtime must not resolve tools during graph construction")

    monkeypatch.setattr(graph_module, "resolve_configured_runtime_tools", fail_if_resolved)
    monkeypatch.setattr(
        graph_module, "create_agent",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    context = ChatBotContext(uid="u1", thread_id="t1", model="test:model")
    context._runtime_prepared = True
    context.runtime_tools = (injected_tool,)
    agent = ChatbotAgent(model_provider=object())

    await agent.get_graph(context=context)

    assert captured["tools"] == [injected_tool]


@pytest.mark.asyncio
async def test_tool_timeout_is_recoverable():
    import asyncio
    from datadeck.agents.middlewares.tool_timeout import ToolTimeoutMiddleware

    middleware = ToolTimeoutMiddleware(0.01)
    request = SimpleNamespace(tool_call={"id": "call-1", "name": "slow_tool"})

    async def slow_handler(_request):
        await asyncio.sleep(1)

    result = await middleware.awrap_tool_call(request, slow_handler)

    assert result.status == "error"
    assert result.tool_call_id == "call-1"
    assert "超过" in result.content
