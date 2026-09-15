"""Make thread creation idempotent per user."""
from alembic import op

revision = "20260913_0009"
down_revision = "20260911_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE threads ADD COLUMN IF NOT EXISTS request_id VARCHAR(64)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_threads_uid_request_id "
        "ON threads(uid, request_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_threads_uid_request_id")
    op.execute("ALTER TABLE threads DROP COLUMN IF EXISTS request_id")
