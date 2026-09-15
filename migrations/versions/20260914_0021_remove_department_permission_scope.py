"""Remove the retired department resource-permission scope.

Department management is no longer part of DataDeck.  Existing department
scopes are intentionally migrated to global scope because the current sharing
model exposes only global and explicit-user access.
"""

import json
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260914_0021"
down_revision: str | None = "20260914_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_without_department(scope: object) -> object:
    if not isinstance(scope, dict):
        return scope
    if scope.get("access_level") == "department":
        return {"access_level": "global", "user_uids": []}
    return {
        "access_level": scope.get("access_level", "global"),
        "user_uids": [
            str(value).strip()
            for value in scope.get("user_uids") or []
            if str(value).strip()
        ],
    }


def _config_without_department(raw_config: object) -> dict | None:
    if isinstance(raw_config, dict):
        config = dict(raw_config)
    else:
        try:
            config = json.loads(raw_config or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(config, dict) or config.get("version") != 2:
        return None
    config["read_scope"] = _scope_without_department(config.get("read_scope"))
    config["manage_scope"] = _scope_without_department(config.get("manage_scope"))
    return config


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("agents", "skills"):
        rows = bind.execute(text(f"SELECT id, share_config FROM {table}")).fetchall()
        for resource_id, raw_config in rows:
            config = _config_without_department(raw_config)
            if config is None:
                continue
            bind.execute(
                text(f"UPDATE {table} SET share_config = CAST(:config AS jsonb) WHERE id = :id"),
                {"config": json.dumps(config, ensure_ascii=False), "id": resource_id},
            )


def downgrade() -> None:
    # The retired department membership cannot be reconstructed from the
    # normalized global/user representation; no downgrade is provided.
    pass
