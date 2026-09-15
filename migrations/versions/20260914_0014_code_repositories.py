"""Add Git code repositories attached to knowledge bases."""
from alembic import op

revision = "20260914_0014"
down_revision = "20260914_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS code_repositories (
        id VARCHAR(64) PRIMARY KEY,
        kb_id VARCHAR(64) NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
        uid VARCHAR(64) NOT NULL,
        name VARCHAR(128) NOT NULL,
        repo_url VARCHAR(1024) NOT NULL,
        branch VARCHAR(256) NOT NULL DEFAULT 'main',
        subdir VARCHAR(512) NOT NULL DEFAULT '',
        ssh_key_encrypted TEXT NOT NULL DEFAULT '',
        access_token_encrypted TEXT NOT NULL DEFAULT '',
        local_path VARCHAR(1024) NOT NULL,
        last_commit VARCHAR(128) NOT NULL DEFAULT '',
        last_sync_at TIMESTAMP NULL,
        sync_status VARCHAR(32) NOT NULL DEFAULT 'never',
        sync_error TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_code_repositories_kb ON code_repositories(kb_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_code_repositories_uid ON code_repositories(uid)")
    op.execute("ALTER TABLE code_repositories ADD COLUMN IF NOT EXISTS access_token_encrypted TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS code_repositories")
