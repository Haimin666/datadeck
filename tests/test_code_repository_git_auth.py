from types import SimpleNamespace


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
