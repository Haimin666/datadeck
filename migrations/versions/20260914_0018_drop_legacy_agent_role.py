"""Remove the duplicated is_subagent role flag after execution_role migration."""

from alembic import op


revision = "20260914_0018"
down_revision = "20260914_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'agents'
                  AND column_name = 'is_subagent'
            ) THEN
                UPDATE agents SET execution_role='subagent'
                WHERE is_subagent = TRUE AND execution_role <> 'subagent';
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS is_subagent")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS "
        "is_subagent BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "UPDATE agents SET is_subagent=TRUE WHERE execution_role='subagent'"
    )
