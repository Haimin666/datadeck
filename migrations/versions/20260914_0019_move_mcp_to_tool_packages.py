"""Move the legacy MCP selection into the canonical tool package selection."""

import json

from alembic import op
from sqlalchemy import text


revision = "20260914_0019"
down_revision = "20260914_0018"
branch_labels = None
depends_on = None

MCP_PACKAGE_PREFIX = "package:mcp:"


def _mcp_package(slug: object) -> str | None:
    value = str(slug or "").strip()
    if not value or ":" in value:
        return None
    return f"{MCP_PACKAGE_PREFIX}{value}"


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT id, config_json FROM agents")).fetchall()
    for agent_id, raw_config in rows:
        config = raw_config if isinstance(raw_config, dict) else json.loads(raw_config or "{}")
        context = config.get("context") if isinstance(config.get("context"), dict) else config
        if not isinstance(context, dict) or "mcps" not in context:
            continue

        legacy_mcps = context.pop("mcps", None)
        packages = []
        selected_tools = context.get("tools")
        if selected_tools is None and isinstance(legacy_mcps, list) and legacy_mcps:
            # None means the default core tools. Make that default explicit before
            # adding a dynamic MCP package, otherwise an explicit list would hide
            # the old core tools.
            packages.append("package:core")
        if isinstance(selected_tools, list):
            packages.extend(item.strip() for item in selected_tools if isinstance(item, str) and item.strip())
        if isinstance(legacy_mcps, list):
            packages.extend(
                package for item in legacy_mcps
                if (package := _mcp_package(item)) is not None
            )
        if packages:
            context["tools"] = list(dict.fromkeys(packages))

        bind.execute(text(
            "UPDATE agents SET config_json = CAST(:config AS jsonb) WHERE id = :id"
        ), {
            "config": json.dumps(config, ensure_ascii=False),
            "id": agent_id,
        })


def downgrade() -> None:
    # The old context.mcps shape is intentionally not restored. Runtime config
    # has one canonical package field after this migration.
    pass
