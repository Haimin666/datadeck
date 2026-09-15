from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool

from server.services.agent_runtime_tools import build_knowledge_search_tool
from server.services.agent_runtime_tools import build_agent_runtime_tools
from server.services.agent_runtime_contract import RuntimeResource


class _DbResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Db:
    async def execute(self, _statement):
        return _DbResult([SimpleNamespace(
            id="private-kb",
            name="私有知识库",
            collection_name="collection-private",
        )])


class _Session:
    async def __aenter__(self):
        return _Db()

    async def __aexit__(self, *_args):
        return False


class _Snapshot:
    def mounted_resources(self, kind):
        if kind != "knowledges":
            return ()
        return (RuntimeResource(
            kind="knowledges",
            key="private-kb",
            name="私有知识库",
            metadata={"collection_name": "collection-private"},
        ),)


@pytest.mark.asyncio
async def test_rag_uses_runtime_snapshot_authorization(monkeypatch):
    import server.db as db_module
    import server.services.knowledge_service as knowledge_service

    captured = {}

    async def fake_search(db, uid, kb_id, query, top_k, *, authorized_kb_ids=None):
        captured.update({
            "uid": uid,
            "kb_id": kb_id,
            "query": query,
            "top_k": top_k,
            "authorized_kb_ids": authorized_kb_ids,
        })
        return {"strategy": "keyword", "results": [{"content": "命中"}]}

    monkeypatch.setattr(db_module, "session_context", lambda: _Session())
    monkeypatch.setattr(knowledge_service, "search", fake_search)

    async def placeholder(**_kwargs):
        return {}

    base_tool = StructuredTool.from_function(
        coroutine=placeholder,
        name="rag_search",
        description="检索知识库",
    )
    context = SimpleNamespace(
        knowledge_base_collections=["collection-private"],
        _runtime_snapshot=_Snapshot(),
        runtime_permissions=("knowledge",),
    )
    tool = build_knowledge_search_tool(
        context,
        SimpleNamespace(uid="another-user"),
        base_tool,
    )

    result = await tool.coroutine(query="业务口径", top_k=3)

    assert result["results"][0]["content"] == "命中"
    assert captured == {
        "uid": "another-user",
        "kb_id": "private-kb",
        "query": "业务口径",
        "top_k": 3,
        "authorized_kb_ids": {"private-kb"},
    }


@pytest.mark.asyncio
async def test_explicit_parent_subagent_mount_bypasses_duplicate_role_assignment(monkeypatch):
    import server.db as db_module
    import server.services.agent_runtime_tools as runtime_tools
    import server.services.run_service as run_service

    class FakeDb:
        async def get(self, _model, _role):
            return SimpleNamespace(is_builtin=False, agent_slugs=[])

        async def scalar(self, _statement):
            return SimpleNamespace(id="child-id", slug="child", execution_role="subagent")

        def add(self, _item):
            return None

        async def flush(self):
            return None

    class FakeSession:
        async def __aenter__(self):
            return FakeDb()

        async def __aexit__(self, *_args):
            return False

    async def create_run(**_kwargs):
        return SimpleNamespace(id="child-run")

    dispatched = []

    async def dispatch(run_id):
        dispatched.append(run_id)

    monkeypatch.setattr(db_module, "session_context", lambda: FakeSession())
    monkeypatch.setattr(runtime_tools, "session_context", lambda: FakeSession())
    monkeypatch.setattr(run_service, "create_agent_run", create_run)
    monkeypatch.setattr(run_service, "dispatch_run", dispatch)

    context = SimpleNamespace(
        uid="u1",
        role="limited",
        thread_id="parent-thread",
        run_id="parent-run",
        subagent_depth=0,
        tools=["subagent_start"],
        skills=[],
        subagents=["child"],
        delegation_enabled=True,
        agent_backend_id="ChatbotAgent",
    )
    tool = next(
        item for item in build_agent_runtime_tools(context, SimpleNamespace(uid="u1", role="limited"))
        if item.name == "subagent_start"
    )

    result = await tool.coroutine(subagent_slug="child", task="执行子任务")

    assert result["status"] == "started"
    assert result["run_id"] == "child-run"
    assert dispatched == ["child-run"]
