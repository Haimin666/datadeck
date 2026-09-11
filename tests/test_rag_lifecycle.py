from datadeck.agents.toolkits import rag_store


def test_bm25_collection_can_be_rebuilt_without_leaking_old_chunks(monkeypatch):
    collection = "test-rag-lifecycle"
    monkeypatch.setattr(rag_store, "embedding_configured", lambda: False)
    rag_store.clear_bm25_collection(collection)
    rag_store.register_document_chunks(collection, "doc-1", "旧文档", ["旧口径内容"])
    assert rag_store.search("旧口径", collection_name=collection)["count"] == 1

    removed = rag_store.clear_bm25_collection(collection)
    assert removed == 1
    assert rag_store.search("旧口径", collection_name=collection)["strategy"] == "empty"

    rag_store.register_document_chunks(collection, "doc-2", "新文档", ["新口径内容"])
    result = rag_store.search("新口径", collection_name=collection)
    assert result["count"] == 1
    assert result["results"][0]["doc_id"] == "doc-2"
    rag_store.clear_bm25_collection(collection)


def test_add_document_removes_stale_vector_when_embedding_fails(monkeypatch):
    collection = "test-rag-vector-failure"
    deleted = []
    monkeypatch.setattr(rag_store, "embedding_configured", lambda: True)
    monkeypatch.setattr(rag_store, "ensure_collection", lambda _name: True)
    monkeypatch.setattr(rag_store, "embed_texts", lambda _texts: None)
    monkeypatch.setattr(
        rag_store, "_delete_qdrant_document", lambda name, doc: deleted.append((name, doc)) or True
    )
    rag_store.clear_bm25_collection(collection)

    result = rag_store.add_document("doc-3", "文档", "新内容", collection_name=collection)

    assert result["ok"] is True
    assert deleted == [(collection, "doc-3")]
    rag_store.clear_bm25_collection(collection)
