"""将历史运行时 DDL 收敛到 Alembic。"""
from alembic import op

revision = "20260910_0004"
down_revision = "20260910_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 兼容 0001 之前已经存在的数据库；新库由 0001/0003 直接创建完整字段。
    for ddl in (
        "ALTER TABLE threads ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)",
        "ALTER TABLE threads ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'active'",
        "ALTER TABLE threads ADD COLUMN IF NOT EXISTS extra_metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE threads ADD COLUMN IF NOT EXISTS tool_approval_mode VARCHAR(32) NOT NULL DEFAULT 'default'",
        "CREATE INDEX IF NOT EXISTS ix_threads_project_id ON threads(project_id)",
        "CREATE INDEX IF NOT EXISTS ix_threads_status ON threads(status)",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS icon VARCHAR(512)",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS share_config JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_subagent BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS last_task_id VARCHAR(64)",
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMP",
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS last_status VARCHAR(32)",
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)",
        "CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_project_id ON scheduled_tasks(project_id)",
    ):
        op.execute(ddl)


def downgrade() -> None:
    # 这些字段可能来自旧版本，回滚不删除用户数据。
    pass
