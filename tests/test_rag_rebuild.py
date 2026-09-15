import pytest

from server.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from server.services import knowledge_service


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _RebuildDb:
    def __init__(self, document, knowledge_base, chunks):
        self.document = document
        self.knowledge_base = knowledge_base
        self.chunks = chunks
        self.added = []
        self.deleted = False
        self.flushed = False

    async def execute(self, statement):
        sql = str(statement)
        if "FROM knowledge_documents" in sql:
            return _Result([(self.document, self.knowledge_base)])
        if "FROM knowledge_chunks" in sql:
            if "DELETE" in sql:
                self.deleted = True
                return _Result([])
            return _Result(self.chunks)
        raise AssertionError(f"unexpected SQL in rebuild test: {sql}")

    def add_all(self, items):
        self.added.extend(items)

    async def flush(self):
        self.flushed = True


@pytest.mark.asyncio
async def test_rebuild_rag_indexes_repairs_corrupt_chunks_from_document_fact(monkeypatch):
    document = KnowledgeDocument(
        id="doc-1", kb_id="kb-1", filename="guide.md",
        content="第一段\n\n第二段", chunk_count=2, status="indexed_keyword",
    )
    knowledge_base = KnowledgeBase(
        id="kb-1", uid="u1", name="知识库", collection_name="collection-1",
    )
    stale = KnowledgeChunk(
        id="doc-1#c0", kb_id="kb-1", document_id="doc-1", chunk_index=0,
        content="旧内容",
    )
    db = _RebuildDb(document, knowledge_base, [stale])
    monkeypatch.setattr(knowledge_service, "_embedding_configured", lambda: False)

    repaired = await knowledge_service.rebuild_rag_indexes(db)

    assert repaired == 1
    assert db.deleted is True
    assert [item.content for item in db.added] == ["第一段\n第二段"]
    assert document.chunk_count == 1
    assert db.flushed is True


def test_qdrant_document_count_distinguishes_missing_collection(monkeypatch):
    class FakeClient:
        def collection_exists(self, _name):
            return False

    import qdrant_client

    monkeypatch.setattr(qdrant_client, "QdrantClient", lambda **_kwargs: FakeClient())

    assert knowledge_service._qdrant_document_count("missing", "doc-1") == 0


def test_qdrant_document_count_returns_none_when_unavailable(monkeypatch):
    import qdrant_client

    def fail(**_kwargs):
        raise OSError("qdrant unavailable")

    monkeypatch.setattr(qdrant_client, "QdrantClient", fail)

    assert knowledge_service._qdrant_document_count("collection", "doc-1") is None
