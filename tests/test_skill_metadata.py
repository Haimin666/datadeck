from __future__ import annotations

from pathlib import Path

from server.services.skills.service import parse_skill_dir_metadata


def test_parse_skill_metadata_accepts_scalar_dependencies(tmp_path: Path):
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: Test Skill\n"
        "slug: test-skill\n"
        "description: A test skill.\n"
        "tool_dependencies: shell\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )

    parsed = parse_skill_dir_metadata(skill_dir)

    assert parsed["tool_dependencies"] == ["shell"]
