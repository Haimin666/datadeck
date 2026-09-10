"""Add persistent basic text knowledge bases and documents."""
from alembic import op

revision = "20260910_0002"
down_revision = "20260910_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 基线迁移会在全新数据库中通过 metadata.create_all 一并创建这些表；
    # IF NOT EXISTS 使本迁移也能安全接管已存在的数据库。
    op.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_bases (
            id VARCHAR(64) PRIMARY KEY,
            uid VARCHAR(64) NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
            name VARCHAR(128) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            embedding_model VARCHAR(256) NOT NULL DEFAULT 'BAAI/bge-m3',
            collection_name VARCHAR(128) NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_bases_uid ON knowledge_bases(uid)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id VARCHAR(64) PRIMARY KEY,
            kb_id VARCHAR(64) NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
            filename VARCHAR(512) NOT NULL,
            content TEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            chunk_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_documents_kb_id ON knowledge_documents(kb_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_documents_kb_status ON knowledge_documents(kb_id, status)")


def downgrade() -> None:
    op.drop_index("ix_knowledge_documents_kb_status", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_kb_id", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    op.drop_index("ix_knowledge_bases_uid", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
