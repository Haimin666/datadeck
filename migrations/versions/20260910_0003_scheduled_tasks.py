"""Add scheduled task definitions."""
from alembic import op

revision = "20260910_0003"
down_revision = "20260910_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id VARCHAR(64) PRIMARY KEY,
            uid VARCHAR(64) NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            cron VARCHAR(128) NOT NULL,
            prompt TEXT NOT NULL,
            agent_slug VARCHAR(128) NOT NULL DEFAULT 'default-chatbot',
            project_id VARCHAR(64) REFERENCES projects(id) ON DELETE SET NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            last_task_id VARCHAR(64),
            last_run_at TIMESTAMP,
            last_status VARCHAR(32),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_uid ON scheduled_tasks(uid)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_uid_enabled ON scheduled_tasks(uid, enabled)")
    for ddl in (
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)",
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS last_task_id VARCHAR(64)",
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMP",
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS last_status VARCHAR(32)",
    ):
        op.execute(ddl)
    op.execute("CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_project_id ON scheduled_tasks(project_id)")


def downgrade() -> None:
    op.drop_index("ix_scheduled_tasks_uid_enabled", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_project_id", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_uid", table_name="scheduled_tasks")
    op.drop_table("scheduled_tasks")
