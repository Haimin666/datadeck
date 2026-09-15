"""Persist structured SQL AST feature evidence for code snapshots."""

from collections.abc import Sequence

from alembic import op


revision: str = "20260915_0023"
down_revision: str | None = "20260915_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE code_repository_files ADD COLUMN IF NOT EXISTS features_json JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE code_repository_files DROP COLUMN IF EXISTS features_json")
