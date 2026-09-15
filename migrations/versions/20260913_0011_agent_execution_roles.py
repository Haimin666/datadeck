"""Separate Agent backend from its collaboration role."""
from alembic import op

revision = "20260913_0011"
down_revision = "20260913_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS execution_role VARCHAR(32) NOT NULL DEFAULT 'standalone'")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS delegation_enabled BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("UPDATE agents SET execution_role = 'subagent' WHERE is_subagent = TRUE")


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS delegation_enabled")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS execution_role")
