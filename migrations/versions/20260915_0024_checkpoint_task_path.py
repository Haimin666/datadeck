"""Align LangGraph checkpoint writes with the current saver schema."""

from collections.abc import Sequence

from alembic import op


revision: str = "20260915_0024"
down_revision: str | None = "20260915_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # langgraph-checkpoint-postgres >= 3 writes task_path for nested/parallel tasks.
    # Checkpoint tables belong to langgraph and are created by AsyncPostgresSaver.setup.
    # Alembic runs before the app initializes the saver on a fresh database, so this
    # migration must be a no-op until those tables exist.
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('public.checkpoint_writes') IS NOT NULL THEN "
        "ALTER TABLE checkpoint_writes "
        "ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT ''; "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('public.checkpoint_writes') IS NOT NULL THEN "
        "ALTER TABLE checkpoint_writes DROP COLUMN IF EXISTS task_path; "
        "END IF; END $$"
    )
