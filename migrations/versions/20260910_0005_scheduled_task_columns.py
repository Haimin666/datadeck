"""补齐已标记迁移但实际缺失的定时任务列。"""
from alembic import op

revision = "20260910_0005"
down_revision = "20260910_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for ddl in (
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)",
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS last_task_id VARCHAR(64)",
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMP",
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS last_status VARCHAR(32)",
        "CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_project_id ON scheduled_tasks(project_id)",
    ):
        op.execute(ddl)


def downgrade() -> None:
    pass
