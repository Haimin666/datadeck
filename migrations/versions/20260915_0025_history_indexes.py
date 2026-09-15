"""Add indexes used by the persisted chat history query."""

from collections.abc import Sequence

from alembic import op


revision: str = "20260915_0025"
down_revision: str | None = "20260915_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('public.agent_runs') IS NOT NULL THEN "
        "CREATE INDEX IF NOT EXISTS ix_agent_runs_thread_uid_created "
        "ON agent_runs(thread_id, uid, created_at); "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('public.run_events') IS NOT NULL THEN "
        "CREATE INDEX IF NOT EXISTS ix_run_events_run_seq "
        "ON run_events(run_id, seq); "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_run_events_run_seq")
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_thread_uid_created")
