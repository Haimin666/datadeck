"""Add indexes used by material and metric registry list pages."""
from alembic import op

revision = "20260911_0008"
down_revision = "20260911_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_materials_type_layer_updated "
        "ON agent_materials(source_type, layer, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_materials_updated_at "
        "ON agent_materials(updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_metric_registry_status_conflict_updated "
        "ON metric_registry(status, conflict_status, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_metric_registry_updated_at "
        "ON metric_registry(updated_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_materials_type_layer_updated")
    op.execute("DROP INDEX IF EXISTS ix_agent_materials_updated_at")
    op.execute("DROP INDEX IF EXISTS ix_metric_registry_status_conflict_updated")
    op.execute("DROP INDEX IF EXISTS ix_metric_registry_updated_at")
