"""Normalize Agent tool selections to capability package slugs."""
import json

from alembic import op
from sqlalchemy import text

revision = "20260914_0015"
down_revision = "20260914_0014"
branch_labels = None
depends_on = None

TOOL_TO_PACKAGE = {
    "metric_lookup": "package:knowledge", "rag_search": "package:knowledge",
    "omd_list_databases": "package:omd", "omd_search_tables": "package:omd",
    "omd_list_tables": "package:omd", "omd_get_table_schema": "package:omd",
    "omd_get_table_lineage": "package:omd", "sql_validate": "package:sql",
    "sql_execute_query": "package:sql", "subagent_start": "package:subagents",
    "subagent_status": "package:subagents", "subagent_events": "package:subagents",
    "subagent_cancel": "package:subagents", "subagent_await": "package:subagents",
    "subagent_orchestrate": "package:subagents",
}
PACKAGE_SLUGS = {
    "package:core", "package:platform", "package:subagents", "package:sql", "package:omd", "package:knowledge",
}


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT id, config_json FROM agents")).fetchall()
    for agent_id, raw_config in rows:
        config = raw_config if isinstance(raw_config, dict) else json.loads(raw_config or "{}")
        context = config.get("context") if isinstance(config.get("context"), dict) else config
        selected = context.get("tools") if isinstance(context, dict) else None
        if not isinstance(selected, list):
            continue
        normalized = []
        for item in selected:
            value = str(item)
            package = value if value in PACKAGE_SLUGS else TOOL_TO_PACKAGE.get(value)
            if package and package not in normalized:
                normalized.append(package)
        context["tools"] = normalized
        bind.execute(text("UPDATE agents SET config_json = CAST(:config AS jsonb) WHERE id = :id"), {
            "config": json.dumps(config, ensure_ascii=False), "id": agent_id,
        })


def downgrade() -> None:
    pass
