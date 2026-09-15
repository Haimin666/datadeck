from __future__ import annotations

import asyncio

import pytest

from server.services import run_service


def test_runtime_resource_mentions_are_parsed():
    from server.services.run_service import _runtime_resource_mentions

    assert _runtime_resource_mentions(
        '请参考 @knowledge:"业务 规范" 和 @skill:dba，再参考 @skill:dba'
    ) == (["业务 规范"], ["dba"])


@pytest.mark.asyncio
async def test_pending_run_is_claimed_once_across_dispatchers(monkeypatch):
    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Db:
        def __init__(self):
            self.claimed = False
            self.commits = 0
            self.calls = []

        async def execute(self, statement, _params):
            self.calls.append(str(statement))
            statement_text = str(statement)
            if "SELECT thread_id, uid" in statement_text:
                return Result(("thread-1", "user-1"))
            if "pg_advisory_xact_lock" in statement_text:
                return Result(None)
            assert "status='pending'" in statement_text
            if self.claimed:
                return Result(None)
            self.claimed = True
            return Result(("run-1",))

        async def commit(self):
            self.commits += 1

    class Session:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *_args):
            return False

    db = Db()
    monkeypatch.setattr(run_service, "async_session_factory", lambda: Session(db))

    assert await run_service._claim_pending_run("run-1") is True
    assert await run_service._claim_pending_run("run-1") is False
    assert db.commits == 2
    assert any("pg_advisory_xact_lock" in call for call in db.calls)


def test_old_run_task_callback_does_not_remove_new_task():
    old_task = object()
    new_task = object()
    run_service._running["same-run"] = new_task

    run_service._remove_running_task("same-run", old_task)
    assert run_service._running["same-run"] is new_task

    run_service._remove_running_task("same-run", new_task)
    assert "same-run" not in run_service._running


@pytest.mark.asyncio
async def test_recovery_only_marks_stale_runs(monkeypatch):
    class Result:
        rowcount = 1

    class Db:
        def __init__(self):
            self.statement = None
            self.params = None
            self.commits = 0

        async def execute(self, statement, params):
            self.statement = str(statement)
            self.params = params
            return Result()

        async def commit(self):
            self.commits += 1

    class Session:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *_args):
            return False

    db = Db()
    monkeypatch.setattr(run_service, "async_session_factory", lambda: Session(db))
    monkeypatch.setenv("DATADECK_AGENT_RUN_TIMEOUT", "180")

    assert await run_service.recover_orphaned_agent_runs() == 1
    assert "updated_at < :stale_before" in db.statement
    assert db.params["stale_before"] < db.params["now"]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_cancel_race_does_not_publish_after_terminal_transition(monkeypatch):
    class Result:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Db:
        async def execute(self, statement, _params):
            statement_text = str(statement)
            if "SELECT thread_id, status" in statement_text:
                return Result(("thread-1", "running"))
            if "UPDATE agent_runs SET status='cancelled'" in statement_text:
                return Result(None)
            raise AssertionError(f"unexpected SQL: {statement_text}")

        async def scalar(self, statement, _params):
            assert "SELECT status FROM agent_runs" in str(statement)
            return "completed"

        async def commit(self):
            pass

    class Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    db = Db()
    events = []
    monkeypatch.setattr(run_service, "async_session_factory", lambda: Session())

    async def record_event(*args):
        events.append(args)

    monkeypatch.setattr(run_service, "append_event", record_event)
    monkeypatch.setattr(run_service, "_dispatch_next_queued", lambda *_args: None)

    assert await run_service.request_cancel("run-1", "user-1") == "completed"
    assert events == []


@pytest.mark.asyncio
async def test_shutdown_waits_for_running_agent_tasks(monkeypatch):
    started = asyncio.Event()

    async def wait_forever(run_id, *, resume_command=None):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(run_service, "_execute_run", wait_forever)

    await run_service.dispatch_run("shutdown-test")
    await started.wait()
    task = run_service._running["shutdown-test"]
    await run_service.shutdown_running_agent_runs()

    assert task.done()
    assert task.cancelled()
    assert run_service._running == {}


@pytest.mark.asyncio
async def test_shutdown_finalizes_owned_run_and_publishes_terminal_events(monkeypatch):
    class Result:
        def fetchone(self):
            return ("thread-1",)

    class Db:
        async def execute(self, statement, _params):
            assert "status='failed'" in str(statement)
            assert "status IN ('running','cancel_requested')" in str(statement)
            return Result()

        async def commit(self):
            pass

    class Session:
        async def __aenter__(self):
            return Db()

        async def __aexit__(self, *_args):
            return False

    events = []
    monkeypatch.setattr(run_service, "async_session_factory", lambda: Session())

    async def record_event(*args):
        events.append(args)

    monkeypatch.setattr(run_service, "append_event", record_event)

    await run_service._mark_run_failed_after_shutdown("run-1")

    assert [event[1] for event in events] == ["error", "end"]
    assert all(event[3] == "thread-1" for event in events)


@pytest.mark.asyncio
async def test_rag_recovery_commits_after_startup_in_background(monkeypatch):
    import server.main as main

    class Db:
        def __init__(self):
            self.commits = 0

        async def commit(self):
            self.commits += 1

    class Session:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *_args):
            return False

    db = Db()
    recovered = []

    async def rebuild(index_db):
        assert index_db is db
        recovered.append(True)
        return 3

    monkeypatch.setattr(main, "async_session_factory", lambda: Session(db))
    monkeypatch.setattr(
        "server.services.knowledge_service.rebuild_rag_indexes", rebuild,
    )

    await main._rebuild_rag_indexes_in_background()

    assert recovered == [True]
    assert db.commits == 1
