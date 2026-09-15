from __future__ import annotations

from pathlib import Path

import pytest

from server.services.skills.service import parse_skill_dir_metadata


def test_parse_skill_metadata_accepts_scalar_dependencies(tmp_path: Path):
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: Test Skill\n"
        "slug: test-skill\n"
        "description: A test skill.\n"
        "version: 2.1.0\n"
        "tool_dependencies: shell\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )

    parsed = parse_skill_dir_metadata(skill_dir)

    assert parsed["tool_dependencies"] == ["shell"]
    assert parsed["version"] == "2.1.0"


def test_personal_skill_keeps_declared_dependencies(tmp_path: Path):
    from server.services.skills.service import _resolved_personal_skill

    metadata = {
        "slug": "dba",
        "name": "DBA",
        "description": "database helper",
        "tool_dependencies": ["execute"],
        "mcp_dependencies": ["omd"],
        "skill_dependencies": ["helper"],
    }

    resolved = _resolved_personal_skill("user-1", tmp_path, metadata)

    assert resolved.tool_dependencies == ["execute"]
    assert resolved.mcp_dependencies == ["omd"]
    assert resolved.skill_dependencies == ["helper"]


def test_runtime_skill_keeps_script_source_dir(tmp_path: Path):
    from types import SimpleNamespace
    from server.services.skills.runtime import build_runtime_skills

    item = SimpleNamespace(
        slug="dba", name="DBA", description="database helper", source_scope="personal",
        source_dir=tmp_path, tool_dependencies=["run_skill_script"],
        mcp_dependencies=[], skill_dependencies=[],
    )

    runtime = build_runtime_skills([item])

    assert runtime["dba"]["source_dir"] == str(tmp_path)
    assert runtime["dba"]["tools"] == ["run_skill_script"]


def test_skill_share_options_use_global_or_user_scope_only():
    from types import SimpleNamespace

    from server.services.skills.service import get_allowed_skill_access_levels

    assert get_allowed_skill_access_levels(SimpleNamespace(role="admin")) == ["global", "user"]
    assert get_allowed_skill_access_levels(SimpleNamespace(role="user")) == ["user"]


def test_resource_permission_rejects_retired_department_scope():
    from server.permissions import normalize_permission_config

    normalized = normalize_permission_config({
        "version": 2,
        "read_scope": {"access_level": "global", "user_uids": []},
        "manage_scope": None,
    })
    assert normalized["read_scope"] == {"access_level": "global", "user_uids": []}

    with pytest.raises(ValueError, match="无效的资源权限范围"):
        normalize_permission_config({
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [1]},
            "manage_scope": None,
        })


def test_production_settings_reject_example_jwt_secret(monkeypatch):
    from server.config import Settings

    monkeypatch.setenv("DATADECK_ENV", "production")
    monkeypatch.setenv("DATADECK_AGENT_ALLOW_ALL_ACTIONS", "false")

    with pytest.raises(ValueError, match="示例值"):
        Settings(jwt_secret_key="please-change-to-a-random-string-at-least-32-characters")

    configured = Settings(jwt_secret_key="x" * 32)
    assert configured.jwt_secret_key == "x" * 32
