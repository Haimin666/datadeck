from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.services.agent_runtime_tools import build_agent_runtime_tools


@pytest.mark.asyncio
async def test_uploaded_text_attachment_is_available_as_runtime_tool(monkeypatch, tmp_path):
    from server.services import attachment_service

    thread_dir = tmp_path / "thread-1"
    thread_dir.mkdir()
    (thread_dir / "upload.txt").write_text("真实附件内容", encoding="utf-8")
    monkeypatch.setattr(attachment_service, "STORAGE_ROOT", str(tmp_path))

    context = SimpleNamespace(
        uid="user-1",
        thread_id="thread-1",
        tools=None,
        attachments=[{
            "id": 7,
            "file_id": "7",
            "file_name": "upload.txt",
            "object_name": "upload.txt",
        }],
        _effective_skill_slugs=[],
        agent_backend_id="ChatbotAgent",
        subagent_depth=0,
        delegation_enabled=False,
        subagents=None,
        workdir_path="",
    )

    tools = build_agent_runtime_tools(context, SimpleNamespace(uid="user-1", role="user"))
    reader = next(tool for tool in tools if tool.name == "read_attachment")

    result = await reader.ainvoke({"file_id": "7"})

    assert result == "真实附件内容"


@pytest.mark.asyncio
async def test_present_artifacts_copies_outputs_into_thread_storage(monkeypatch, tmp_path):
    from server.services import attachment_service
    from server.workspace.temp_workdir import TemporaryWorkdir

    workdir_root = tmp_path / "workdir"
    (workdir_root / "outputs").mkdir(parents=True)
    (workdir_root / "outputs" / "result.json").write_text('{"ok": true}', encoding="utf-8")
    storage_root = tmp_path / "threads"
    monkeypatch.setattr(attachment_service, "STORAGE_ROOT", str(storage_root))

    context = SimpleNamespace(
        uid="user-1", thread_id="thread-1", tools=["package:platform"],
        workdir_path="tmp/probe", workdir=TemporaryWorkdir("tmp/probe", workdir_root),
        _effective_skill_slugs=[], agent_backend_id="ChatbotAgent", subagent_depth=0,
        delegation_enabled=False, subagents=None,
    )
    tools = build_agent_runtime_tools(context, SimpleNamespace(uid="user-1", role="user"))
    presenter = next(tool for tool in tools if tool.name == "present_artifacts")

    result = await presenter.ainvoke({"filepaths": ["/outputs/result.json"]})

    assert result == {"ok": True, "filepaths": ["/outputs/result.json"], "errors": []}
    assert (storage_root / "thread-1" / "outputs" / "result.json").read_text(encoding="utf-8") == '{"ok": true}'


@pytest.mark.asyncio
async def test_skill_script_runs_only_from_mounted_skill(tmp_path):
    from server.workspace.temp_workdir import TemporaryWorkdir

    skill_root = tmp_path / "skill"
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "scripts" / "probe.py").write_text(
        "from pathlib import Path\n"
        "print(Path.cwd().name)\n"
        "print('skill-script-ok')\n",
        encoding="utf-8",
    )
    workdir_root = tmp_path / "workdir"
    workdir_root.mkdir()
    context = SimpleNamespace(
        uid="user-1", thread_id="thread-1", tools=["package:platform"],
        workdir_path="tmp/probe", workdir=TemporaryWorkdir("tmp/probe", workdir_root),
        _effective_skill_slugs=["probe"],
        _runtime_skills={"probe": {"source_dir": str(skill_root)}},
        agent_backend_id="ChatbotAgent", subagent_depth=0,
        delegation_enabled=False, subagents=None,
    )
    tools = build_agent_runtime_tools(context, SimpleNamespace(uid="user-1", role="user"))
    runner = next(tool for tool in tools if tool.name == "run_skill_script")

    result = await runner.ainvoke({
        "skill_slug": "probe", "script_path": "scripts/probe.py", "script_args": [],
    })

    assert result["ok"] is True
    assert "skill-script-ok" in result["output"]
    result = await runner.ainvoke({
        "skill_slug": "probe", "script_path": "scripts/probe.py",
        "script_args": "[]",
    })
    assert result["ok"] is True
    with pytest.raises(Exception, match="scripts"):
        await runner.ainvoke({
            "skill_slug": "probe", "script_path": "../outside.py", "script_args": [],
        })


def test_confirm_attachments_rejects_path_traversal(monkeypatch, tmp_path):
    from server.services import attachment_service

    monkeypatch.setattr(attachment_service, "STORAGE_ROOT", str(tmp_path / "threads"))
    outside = tmp_path / "outside.txt"
    outside.write_text("不得被移动", encoding="utf-8")

    class Db:
        def add(self, _item):
            raise AssertionError("非法对象名不应落库")

    objects, _ = attachment_service.confirm_attachments(
        Db(), "user-1", "thread-1", [{"object_name": "../../outside.txt"}],
    )

    assert objects == []
    assert outside.read_text(encoding="utf-8") == "不得被移动"


def test_remove_thread_storage_does_not_follow_thread_symlink(monkeypatch, tmp_path):
    from server.services import attachment_service

    root = tmp_path / "threads"
    root.mkdir()
    target = root / "other-thread"
    target.mkdir()
    (target / "keep.txt").write_text("保留", encoding="utf-8")
    (root / "thread-1").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(attachment_service, "STORAGE_ROOT", str(root))

    with pytest.raises(ValueError, match="非法线程存储路径"):
        attachment_service.remove_thread_storage("thread-1")

    assert (target / "keep.txt").exists()
