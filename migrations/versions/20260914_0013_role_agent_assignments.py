"""Add role-level Agent assignments."""
from alembic import op

revision = "20260914_0013"
down_revision = "20260914_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE roles ADD COLUMN IF NOT EXISTS agent_slugs JSONB NOT NULL DEFAULT '[]'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE roles DROP COLUMN IF EXISTS agent_slugs")
