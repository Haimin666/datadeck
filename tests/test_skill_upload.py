from __future__ import annotations

import io
import zipfile


def _login(client):
    response = client.post(
        "/api/auth/token", data={"username": "admin", "password": "admin123456"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _skill_zip(frontmatter: str) -> bytes:
    content = f"---\n{frontmatter}---\n\nbody\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("test-skill/SKILL.md", content)
    return buffer.getvalue()


def test_prepare_skill_upload_accepts_valid_zip(app_client):
    archive = _skill_zip(
        "name: Test Skill\nslug: test-skill\ndescription: A test skill.\n"
    )

    response = app_client.post(
        "/api/skills/import/prepare",
        files={"file": ("test-skill.zip", archive, "application/zip")},
        headers=_login(app_client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["items"][0]["slug"] == "test-skill"


def test_prepare_skill_upload_accepts_scalar_dependency(app_client):
    archive = _skill_zip(
        "name: Test Skill\nslug: test-skill\ndescription: A test skill.\n"
        "tool_dependencies: shell\n"
    )

    response = app_client.post(
        "/api/skills/import/prepare",
        files={"file": ("test-skill.zip", archive, "application/zip")},
        headers=_login(app_client),
    )

    assert response.status_code == 200, response.text
    item = response.json()["data"]["items"][0]
    assert item["tool_dependencies"] == ["shell"]
