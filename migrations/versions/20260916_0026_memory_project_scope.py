"""Scope long-term agent memories to an optional project."""

from collections.abc import Sequence

from alembic import op


revision: str = "20260916_0026"
down_revision: str | None = "20260915_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('public.agent_memories') IS NOT NULL THEN "
        "ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS project_id VARCHAR(64); "
        "CREATE INDEX IF NOT EXISTS ix_agent_memories_uid_project "
        "ON agent_memories(uid, project_id); "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_memories_uid_project")
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('public.agent_memories') IS NOT NULL THEN "
        "ALTER TABLE agent_memories DROP COLUMN IF EXISTS project_id; "
        "END IF; END $$"
    )
