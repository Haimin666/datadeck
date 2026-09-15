"""Add per-provider model proxy configuration."""
from alembic import op

revision = "20260914_0012"
down_revision = "20260913_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE model_providers ADD COLUMN IF NOT EXISTS proxy_url VARCHAR(500)")


def downgrade() -> None:
    op.execute("ALTER TABLE model_providers DROP COLUMN IF EXISTS proxy_url")
