"""基础文本知识库：持久化文档，切片后写入 Qdrant。"""
from __future__ import annotations

import re
import os
import uuid
from pathlib import PurePath

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import KnowledgeBase, KnowledgeDocument
from server.utils.datetime_utils import utc_now_naive

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json"}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


def _chunks(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    result: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        while len(paragraph) > chunk_size:
            result.append(paragraph[:chunk_size])
            paragraph = paragraph[chunk_size - overlap:]
        if buffer and len(buffer) + len(paragraph) + 1 > chunk_size:
            result.append(buffer)
            buffer = ""
        buffer = f"{buffer}\n{paragraph}".strip()
    if buffer:
        result.append(buffer)
    return result


def _safe_filename(filename: str) -> str:
    return PurePath(filename or "document.txt").name or "document.txt"


def _decode(filename: str, content: bytes) -> str:
    suffix = PurePath(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"仅支持文本文件：{', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("文档大小不能超过 20 MB")
    return content.decode("utf-8-sig")


async def list_knowledge_bases(db: AsyncSession, uid: str) -> list[dict]:
    rows = (await db.execute(
        select(KnowledgeBase, func.count(KnowledgeDocument.id))
        .outerjoin(KnowledgeDocument, KnowledgeDocument.kb_id == KnowledgeBase.id)
        .where(KnowledgeBase.uid == uid)
        .group_by(KnowledgeBase.id)
        .order_by(KnowledgeBase.updated_at.desc())
    )).all()
    return [item.to_dict(document_count=count) for item, count in rows]


async def get_knowledge_base(db: AsyncSession, uid: str, kb_id: str) -> KnowledgeBase:
    item = (await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.uid == uid)
    )).scalar_one_or_none()
    if item is None:
        raise ValueError("知识库不存在或无权访问")
    return item


async def create_knowledge_base(db: AsyncSession, uid: str, name: str, description: str = "") -> KnowledgeBase:
    name = name.strip()
    if not name:
        raise ValueError("知识库名称不能为空")
    item = KnowledgeBase(
        id=str(uuid.uuid4()), uid=uid, name=name, description=description.strip(),
        collection_name=f"datadeck_kb_{uuid.uuid4().hex}",
    )
    db.add(item)
    await db.flush()
    return item


async def list_documents(db: AsyncSession, kb_id: str, uid: str) -> list[dict]:
    await get_knowledge_base(db, uid, kb_id)
    items = (await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id)
        .order_by(KnowledgeDocument.created_at.desc())
    )).scalars().all()
    return [item.to_dict() for item in items]


async def _index_qdrant(kb: KnowledgeBase, document: KnowledgeDocument, chunks: list[str]) -> bool:
    from datadeck.agents.toolkits.rag_store import embed_texts, embedding_configured

    if not embedding_configured():
        return False
    vectors = embed_texts(chunks)
    if not vectors:
        return False
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    client = QdrantClient(url=os.getenv("DATADECK_QDRANT_URL", "http://localhost:6333"), timeout=10)
    if not client.collection_exists(kb.collection_name):
        client.create_collection(
            collection_name=kb.collection_name,
            vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE),
        )
    client.upsert(
        collection_name=kb.collection_name,
        points=[PointStruct(
            id=str(uuid.uuid4()), vector=vector,
            payload={"document_id": document.id, "filename": document.filename, "content": chunk},
        ) for vector, chunk in zip(vectors, chunks, strict=True)],
    )
    return True


async def add_document(db: AsyncSession, uid: str, kb_id: str, filename: str, content: bytes) -> KnowledgeDocument:
    kb = await get_knowledge_base(db, uid, kb_id)
    filename = _safe_filename(filename)
    text = _decode(filename, content)
    chunks = _chunks(text)
    if not chunks:
        raise ValueError("文档内容不能为空")
    document = KnowledgeDocument(
        id=str(uuid.uuid4()), kb_id=kb.id, filename=filename, content=text,
        status="indexing", chunk_count=len(chunks),
    )
    db.add(document)
    await db.flush()
    try:
        vector_indexed = await _index_qdrant(kb, document, chunks)
        document.status = "indexed" if vector_indexed else "indexed_keyword"
    except Exception as exc:  # noqa: BLE001
        document.status = "indexed_keyword"
        document.error_message = f"向量索引失败，已保留关键词检索：{str(exc)[:300]}"
    document.updated_at = utc_now_naive()
    await db.flush()
    return document


async def delete_document(db: AsyncSession, uid: str, kb_id: str, document_id: str) -> None:
    kb = await get_knowledge_base(db, uid, kb_id)
    document = (await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id, KnowledgeDocument.kb_id == kb.id
        )
    )).scalar_one_or_none()
    if document is None:
        raise ValueError("文档不存在")
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        client = QdrantClient(url=os.getenv("DATADECK_QDRANT_URL", "http://localhost:6333"), timeout=10)
        if client.collection_exists(kb.collection_name):
            client.delete(
                collection_name=kb.collection_name,
                points_selector=Filter(must=[FieldCondition(
                    key="document_id", match=MatchValue(value=document.id)
                )]),
            )
    except Exception:
        pass
    await db.delete(document)


async def delete_knowledge_base(db: AsyncSession, uid: str, kb_id: str) -> None:
    kb = await get_knowledge_base(db, uid, kb_id)
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=os.getenv("DATADECK_QDRANT_URL", "http://localhost:6333"), timeout=10)
        if client.collection_exists(kb.collection_name):
            client.delete_collection(kb.collection_name)
    except Exception:
        pass
    await db.delete(kb)


async def search(db: AsyncSession, uid: str, kb_id: str, query: str, top_k: int = 5) -> dict:
    kb = await get_knowledge_base(db, uid, kb_id)
    query = query.strip()
    if not query:
        raise ValueError("检索内容不能为空")
    from datadeck.agents.toolkits.rag_store import embed_texts, embedding_configured
    if embedding_configured():
        try:
            from qdrant_client import QdrantClient
            client = QdrantClient(url=os.getenv("DATADECK_QDRANT_URL", "http://localhost:6333"), timeout=10)
            if client.collection_exists(kb.collection_name):
                vectors = embed_texts([query])
                if vectors:
                    points = client.query_points(
                        collection_name=kb.collection_name, query=vectors[0], limit=top_k
                    ).points
                    return {"results": [
                        {"document_id": p.payload.get("document_id"), "filename": p.payload.get("filename"),
                         "content": p.payload.get("content", ""), "score": round(float(p.score), 4)}
                        for p in points
                    ], "strategy": "qdrant"}
        except Exception:
            pass
    terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query)]
    documents = (await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb.id)
    )).scalars().all()
    ranked = sorted(
        ((sum(text.count(term) for term in terms), document) for document in documents
         for text in [document.content.lower()]),
        key=lambda item: item[0], reverse=True,
    )
    return {"results": [
        {"document_id": document.id, "filename": document.filename,
         "content": document.content[:1200], "score": score}
        for score, document in ranked[:top_k] if score > 0
    ], "strategy": "keyword"}
