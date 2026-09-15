from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_manage_function(function: str, env_file: Path) -> subprocess.CompletedProcess[str]:
    command = (
        "source ./docker-manage.sh >/dev/null; "
        f'{function} "$1"'
    )
    return subprocess.run(
        ["bash", "-c", command, "bash", str(env_file)],
        cwd=PROJECT_ROOT,
        env={**os.environ, "TMPDIR": str(env_file.parent)},
        text=True,
        capture_output=True,
        check=False,
    )


def _dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def test_first_init_generates_secrets_without_placeholders(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.docker"
    shutil.copy(PROJECT_ROOT / ".env.docker.example", env_file)

    result = _run_manage_function("generate_initial_secrets", env_file)

    assert result.returncode == 0, result.stderr
    values = _dotenv_values(env_file)
    assert len(values["JWT_SECRET_KEY"]) == 64
    assert len(values["POSTGRES_PASSWORD"]) == 48
    assert "please-change" not in values["JWT_SECRET_KEY"]
    assert "please-change" not in values["POSTGRES_PASSWORD"]


def test_invalid_dotenv_line_is_rejected_with_line_number(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.docker"
    env_file.write_text(
        "DATADECK_ENV=production\n(base) terminal output\n",
        encoding="utf-8",
    )

    result = _run_manage_function("validate_dotenv_file", env_file)

    assert result.returncode != 0
    assert ".env.docker 第 2 行不是有效的 KEY=VALUE 配置" in result.stderr
