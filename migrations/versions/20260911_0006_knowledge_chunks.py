"""Persist knowledge-base chunks for restart-safe retrieval and reindexing."""

from alembic import op

revision = "20260911_0006"
down_revision = "20260910_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id VARCHAR(128) PRIMARY KEY,
            kb_id VARCHAR(64) NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
            document_id VARCHAR(64) NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP NOT NULL,
            CONSTRAINT uq_knowledge_chunks_document_index UNIQUE (document_id, chunk_index)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_kb_id ON knowledge_chunks(kb_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_document_id ON knowledge_chunks(document_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_kb_document ON knowledge_chunks(kb_id, document_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")
