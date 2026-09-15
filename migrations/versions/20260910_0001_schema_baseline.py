"""Establish the complete DataDeck schema baseline.

Revision ID: 20260910_0001
Revises:
Create Date: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260910_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create every missing table and align columns added before Alembic adoption."""
    from server.models import Base

    # Import the single model registry before create_all runs.  Runtime services
    # contain behavior only; they must not be imported to register ORM tables.
    import server.models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    for ddl in (
        "ALTER TABLE threads ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)",
        "ALTER TABLE threads ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'active'",
        "ALTER TABLE threads ADD COLUMN IF NOT EXISTS extra_metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE threads ADD COLUMN IF NOT EXISTS tool_approval_mode VARCHAR(32) NOT NULL DEFAULT 'default'",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS icon VARCHAR(512)",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS share_config JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_subagent BOOLEAN NOT NULL DEFAULT FALSE",
    ):
        op.execute(ddl)

    op.execute("CREATE INDEX IF NOT EXISTS ix_threads_project_id ON threads(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_threads_status ON threads(status)")


def downgrade() -> None:
    """A baseline downgrade is intentionally non-destructive."""
    pass
