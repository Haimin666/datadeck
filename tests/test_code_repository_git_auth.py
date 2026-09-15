from types import SimpleNamespace


def test_existing_clone_is_cleaned_before_snapshot_index(monkeypatch, tmp_path):
    import asyncio
    from server.services import code_repository_service as service

    monkeypatch.setenv("DATADECK_DATA_ROOT", str(tmp_path / "data"))
    repo_dir = tmp_path / "data" / "code-repositories" / "repo-1"
    (repo_dir / ".git").mkdir(parents=True)
    repo = SimpleNamespace(
        id="repo-1", repo_url="https://git.example/group/repo.git",
        local_path=str(repo_dir), branch="main", subdir="",
        ssh_key_encrypted="", access_token_encrypted="", sync_status="success",
        sync_error="", last_commit=None, last_sync_at=None,
        to_dict=lambda: {"id": "repo-1"},
    )
    calls = []

    def fake_git(_repo, args, **_kwargs):
        calls.append(args)
        if args[:2] == ["rev-parse", "HEAD"]:
            return "commit-1"
        return "ok"

    class FakeDb:
        async def flush(self):
            return None

    async def fake_get_repo(_db, _uid, _repo_id):
        return repo

    async def fake_index(_db, _repo, _commit):
        return 0

    monkeypatch.setattr(service, "_get_repo", fake_get_repo)
    monkeypatch.setattr(service, "_git", fake_git)
    monkeypatch.setattr(service, "_index_repository_snapshot", fake_index)

    asyncio.run(service.pull_code_repository(FakeDb(), "admin", "repo-1"))

    assert [args[0] for args in calls] == [
        "remote", "fetch", "clean", "reset", "rev-parse",
    ]
    assert calls[2] == ["clean", "-fdx"]


def test_http_access_token_uses_gitlab_basic_auth(monkeypatch, tmp_path):
    import base64
    from server.services import code_repository_service as service

    root = tmp_path / "data" / "code-repositories"
    repo_dir = root / "repo-1"
    monkeypatch.setenv("DATADECK_DATA_ROOT", str(tmp_path / "data"))
    captured = {}

    class Result:
        returncode = 0
        stdout = "ok"

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return Result()

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    repo = SimpleNamespace(
        repo_url="http://git.example/group/repo.git",
        local_path=str(repo_dir),
    )

    service._git(repo, ["clone", repo.repo_url, str(repo_dir)], access_token="token-value")

    expected = base64.b64encode(b"oauth2:token-value").decode("ascii")
    assert captured["env"]["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert captured["env"]["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {expected}"
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
