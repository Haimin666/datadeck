"""Persist DataAgent materials and Ossie metric review fields."""
from alembic import op

revision = "20260911_0007"
down_revision = "20260911_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS agent_materials (
        id VARCHAR(128) PRIMARY KEY,
        source_type VARCHAR(32) NOT NULL,
        source_id VARCHAR(512) NOT NULL,
        title VARCHAR(512) NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        layer VARCHAR(16) NOT NULL DEFAULT 'cold',
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        content_hash VARCHAR(64) NOT NULL,
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        CONSTRAINT uq_agent_materials_source UNIQUE (source_type, source_id)
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_materials_type_layer ON agent_materials(source_type, layer)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_materials_content_hash ON agent_materials(content_hash)")
    for column, ddl in (
        ("ossie_name", "VARCHAR(160)"), ("ossie_expression", "JSONB"),
        ("datatype", "VARCHAR(32)"), ("ai_context", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
        ("source_evidence", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
        ("status", "VARCHAR(32) NOT NULL DEFAULT 'candidate'"),
        ("conflict_status", "VARCHAR(32) NOT NULL DEFAULT 'none'"),
        ("conflict_details", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
        ("updated_at", "TIMESTAMP"),
    ):
        op.execute(f"ALTER TABLE metric_registry ADD COLUMN IF NOT EXISTS {column} {ddl}")
    op.execute("UPDATE metric_registry SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_metric_registry_ossie_name ON metric_registry(ossie_name) WHERE ossie_name IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_materials")
