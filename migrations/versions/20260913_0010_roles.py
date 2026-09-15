"""Add configurable roles and module permissions."""
from alembic import op

revision = "20260913_0010"
down_revision = "20260913_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS roles (
        slug VARCHAR(32) PRIMARY KEY,
        name VARCHAR(64) NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
        is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS roles")
