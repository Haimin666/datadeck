"""Add immutable per-commit code file indexes."""

from collections.abc import Sequence

from alembic import op


revision: str = "20260915_0022"
down_revision: str | None = "20260914_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS code_repository_files (
            id VARCHAR(64) PRIMARY KEY,
            repository_id VARCHAR(64) NOT NULL REFERENCES code_repositories(id) ON DELETE CASCADE,
            commit_sha VARCHAR(128) NOT NULL,
            path VARCHAR(1024) NOT NULL,
            language VARCHAR(32) NOT NULL DEFAULT '',
            size INTEGER NOT NULL DEFAULT 0,
            content_hash VARCHAR(64) NOT NULL DEFAULT '',
            parse_status VARCHAR(32) NOT NULL DEFAULT 'not_sql',
            tables_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            columns_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            indexed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_code_repository_files_snapshot UNIQUE(repository_id, commit_sha, path)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_code_repository_files_repository_path ON code_repository_files(repository_id, path)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_code_repository_files_commit ON code_repository_files(repository_id, commit_sha)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS code_repository_files")
