"""Align LangGraph checkpoint writes with the current saver schema."""

from collections.abc import Sequence

from alembic import op


revision: str = "20260915_0024"
down_revision: str | None = "20260915_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # langgraph-checkpoint-postgres >= 3 writes task_path for nested/parallel tasks.
    # IF NOT EXISTS keeps this safe for databases already upgraded by the saver.
    op.execute(
        "ALTER TABLE checkpoint_writes "
        "ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE checkpoint_writes DROP COLUMN IF EXISTS task_path")
