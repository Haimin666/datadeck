"""Make knowledge-base visibility explicit for runtime and material queries."""

from alembic import op


revision = "20260914_0017"
down_revision = "20260914_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS "
        "access_scope VARCHAR(16) NOT NULL DEFAULT 'shared'"
    )
    op.execute(
        "UPDATE knowledge_bases SET access_scope='shared' "
        "WHERE access_scope IS NULL OR access_scope NOT IN ('private', 'shared', 'public')"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_bases_access_scope "
        "ON knowledge_bases(access_scope)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_bases_access_scope")
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS access_scope")
