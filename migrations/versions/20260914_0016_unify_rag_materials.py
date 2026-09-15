"""Merge legacy AgentMaterial records into the knowledge-document source model."""

from alembic import op


revision = "20260914_0016"
down_revision = "20260914_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column, ddl in (
        ("source_type", "VARCHAR(32) NOT NULL DEFAULT 'upload'"),
        ("source_id", "VARCHAR(512) NOT NULL DEFAULT ''"),
        ("layer", "VARCHAR(16) NOT NULL DEFAULT 'hot'"),
        ("content_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("metadata_json", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
        ("version", "VARCHAR(64) NOT NULL DEFAULT '1'"),
    ):
        op.execute(f"ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS {column} {ddl}")

    # 迁移必须先保留旧物料，再删除旧表。目标 uid 取当前最早的有效用户，
    # 该知识库由共享权限规则对所有 knowledge 模块用户开放。
    op.execute(
        """
        DO $$
        DECLARE target_uid VARCHAR(64);
        BEGIN
            IF to_regclass('public.agent_materials') IS NOT NULL THEN
                SELECT uid INTO target_uid FROM users
                WHERE is_deleted = 0
                ORDER BY CASE WHEN role = 'superadmin' THEN 0
                              WHEN role = 'admin' THEN 1 ELSE 2 END, id
                LIMIT 1;
                IF target_uid IS NOT NULL THEN
                    INSERT INTO knowledge_bases
                        (id, uid, name, description, embedding_model, collection_name, created_at, updated_at)
                    VALUES
                        ('legacy-agent-materials', target_uid, '业务知识库',
                         '从历史 DataAgent 物料一次性迁移的共享知识库。',
                         'BAAI/bge-m3', 'datadeck_kb_legacy_agent_materials',
                         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO NOTHING;

                    INSERT INTO knowledge_documents
                        (id, kb_id, filename, content, source_type, source_id, layer,
                         content_hash, metadata_json, version, status, chunk_count,
                         error_message, created_at, updated_at)
                    SELECT left(md5('agent-material:' || id), 64),
                           'legacy-agent-materials',
                           left(COALESCE(NULLIF(title, ''), source_id), 512),
                           content,
                           source_type,
                           source_id,
                           CASE WHEN layer IN ('hot', 'cold') THEN layer ELSE 'cold' END,
                           content_hash,
                           metadata_json,
                           '1',
                           CASE WHEN status = 'active' THEN 'indexed_keyword' ELSE status END,
                           0,
                           '',
                           created_at,
                           updated_at
                    FROM agent_materials material
                    WHERE NOT EXISTS (
                        SELECT 1 FROM knowledge_documents document
                        WHERE document.kb_id = 'legacy-agent-materials'
                          AND document.source_type = material.source_type
                          AND document.source_id = material.source_id
                    );
                ELSIF EXISTS (SELECT 1 FROM agent_materials) THEN
                    RAISE EXCEPTION 'cannot migrate agent_materials: no valid user exists';
                END IF;
                DROP TABLE agent_materials;
            END IF;
        END $$;
        """
    )
    op.execute("UPDATE knowledge_documents SET source_id = id WHERE source_id = ''")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_documents_source "
        "ON knowledge_documents(kb_id, source_type, source_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_documents_source_type ON knowledge_documents(source_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_documents_layer ON knowledge_documents(layer)")


def downgrade() -> None:
    # 旧 AgentMaterial 数据已合并，不能安全逆向拆分；只回滚索引和新增列。
    op.execute("DROP INDEX IF EXISTS uq_knowledge_documents_source")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_documents_source_type")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_documents_layer")
    for column in ("version", "metadata_json", "content_hash", "layer", "source_id", "source_type"):
        op.execute(f"ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS {column}")
