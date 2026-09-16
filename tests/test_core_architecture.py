"""核心层边界测试：datadeck 可脱离 server 导入和建图校验。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = ROOT / "src" / "datadeck"


def test_core_source_has_no_direct_server_imports():
    for path in CORE_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("from server", "import server")), path


def test_importing_core_does_not_load_server():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys, datadeck; "
            "assert not any(name == 'server' or name.startswith('server.') "
            "for name in sys.modules)"
        )],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_platform_model_provider_uses_injected_catalog_only():
    from datadeck.adapters.platform_model_provider import PlatformModelProvider

    info = SimpleNamespace(
        spec="openai:test",
        provider_type="openai",
        model_id="test",
        base_url="https://example.test/v1",
        api_key="secret",
        proxy_url="",
    )

    class Catalog:
        def get_model_info(self, spec):
            return info if spec == info.spec else None

        def get_all_specs(self, kind=None):
            return [info] if kind in (None, "chat") else []

    provider = PlatformModelProvider(Catalog())
    assert provider.get_model_info(info.spec).base_url == info.base_url
    assert provider.get_all_specs()[0].spec == info.spec


def test_model_adapter_does_not_inherit_ambient_proxy(monkeypatch):
    import httpx
    import langchain_openai

    from datadeck.ports.models import ChatModelSpec, load_chat_model

    created = {}

    class FakeClient:
        def __init__(self, **kwargs):
            created["sync"] = kwargs

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            created["async"] = kwargs

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            created["model"] = kwargs

    monkeypatch.delenv("DATADECK_MODEL_HTTP_PROXY", raising=False)
    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatOpenAI)

    load_chat_model(
        ChatModelSpec(
            spec="openai:test", provider="openai", model="test",
            base_url="https://example.test/v1", api_key="secret",
        ),
        provider=object(),
    )

    assert created["sync"]["trust_env"] is False
    assert created["async"]["trust_env"] is False
    assert "proxy" not in created["sync"]
    assert "proxy" not in created["async"]


def test_temporary_workdir_normalizes_symlinked_host_root(tmp_path):
    from server.workspace.temp_workdir import TemporaryWorkdir

    canonical = tmp_path / "canonical"
    canonical.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(canonical, target_is_directory=True)

    workdir = TemporaryWorkdir("tmp/test", alias)
    result = workdir.write_file("/written.txt", b"ok")

    assert result["path"] == "/written.txt"
    assert (canonical / "written.txt").read_text(encoding="utf-8") == "ok"


def test_temporary_workdir_accepts_relative_and_absolute_virtual_paths(tmp_path):
    from server.workspace.temp_workdir import TemporaryWorkdir

    workdir = TemporaryWorkdir("tmp/test", tmp_path)

    relative = workdir.write_file("outputs/relative.py", b"relative")
    absolute = workdir.write_file("/outputs/absolute.py", b"absolute")

    assert relative["path"] == "/outputs/relative.py"
    assert absolute["path"] == "/outputs/absolute.py"
    assert (tmp_path / "outputs/relative.py").read_bytes() == b"relative"
    assert (tmp_path / "outputs/absolute.py").read_bytes() == b"absolute"


def test_temporary_workdir_rejects_traversal_for_relative_paths(tmp_path):
    from server.workspace.temp_workdir import TemporaryWorkdir

    workdir = TemporaryWorkdir("tmp/test", tmp_path)
    with pytest.raises(ValueError, match="invalid temporary Workdir path"):
        workdir.write_file("outputs/../outside.py", b"blocked")


def test_persistent_workdir_accepts_relative_and_absolute_virtual_paths():
    from server.workspace.workdir import Workdir

    workdir = Workdir("projects/demo", object())

    assert workdir.resolve_path("outputs/result.py") == "/projects/demo/outputs/result.py"
    assert workdir.resolve_path("/outputs/result.py") == "/projects/demo/outputs/result.py"
    with pytest.raises(ValueError, match="invalid Workdir scope path"):
        workdir.resolve_path("outputs/../outside.py")


@pytest.mark.asyncio
async def test_unprepared_graph_requires_runtime_context():
    from datadeck.agents.buildin.chatbot.graph import ChatbotAgent

    agent = ChatbotAgent(model_provider=object())
    with pytest.raises(RuntimeError, match="AgentRuntimeAssembler"):
        await agent.get_graph()
