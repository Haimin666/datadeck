"""基础文本知识库：持久化文档，切片后写入 Qdrant。"""
from __future__ import annotations

import re
import os
import asyncio
import uuid
import hashlib
from pathlib import PurePath

from sqlalchemy import and_, delete as sa_delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from server.utils.datetime_utils import utc_now_naive

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json"}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
# 普通知识库只展示/检索面向业务的文档；原始代码、OMD 快照和抽取中间产物
# 由公共物料空间或专用工具访问，不能污染默认 RAG。
BUSINESS_SOURCE_TYPES = {"upload", "wiki", "business_doc"}


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


async def list_knowledge_bases(
    db: AsyncSession,
    uid: str,
    *,
    allowed_ids: set[str] | None = None,
) -> list[dict]:
    configured_ids = {str(item).strip() for item in (allowed_ids or set()) if str(item).strip()}
    access_filter = or_(
        KnowledgeBase.uid == uid,
        KnowledgeBase.access_scope.in_(("shared", "public")),
        KnowledgeBase.id.in_(configured_ids) if configured_ids else False,
    )
    rows = (await db.execute(
        select(KnowledgeBase, func.count(KnowledgeDocument.id))
        .outerjoin(KnowledgeDocument, and_(
            KnowledgeDocument.kb_id == KnowledgeBase.id,
            KnowledgeDocument.source_type.in_(BUSINESS_SOURCE_TYPES),
        ))
        .where(access_filter)
        .group_by(KnowledgeBase.id)
        .order_by(KnowledgeBase.updated_at.desc())
    )).all()
    return [item.to_dict(document_count=count) for item, count in rows]


async def get_knowledge_base(
    db: AsyncSession,
    uid: str,
    kb_id: str,
    *,
    authorized_ids: set[str] | None = None,
) -> KnowledgeBase:
    explicit_ids = {str(item).strip() for item in (authorized_ids or set()) if str(item).strip()}
    access_filter = or_(
        KnowledgeBase.uid == uid,
        KnowledgeBase.access_scope.in_(("shared", "public")),
        KnowledgeBase.id.in_(explicit_ids) if explicit_ids else False,
    )
    item = (await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            access_filter,
        )
    )).scalar_one_or_none()
    if item is None:
        raise ValueError("知识库不存在或无权访问")
    return item


async def create_knowledge_base(
    db: AsyncSession, uid: str, name: str, description: str = "", access_scope: str = "shared",
) -> KnowledgeBase:
    name = name.strip()
    if not name:
        raise ValueError("知识库名称不能为空")
    if access_scope not in {"private", "shared", "public"}:
        raise ValueError("知识库范围只能是 private、shared 或 public")
    item = KnowledgeBase(
        id=str(uuid.uuid4()), uid=uid, name=name, description=description.strip(),
        access_scope=access_scope,
        collection_name=f"datadeck_kb_{uuid.uuid4().hex}",
    )
    db.add(item)
    await db.flush()
    return item


async def rebuild_rag_indexes(db: AsyncSession) -> int:
    """从 PostgreSQL 修复持久化切片，并按需恢复 Qdrant 向量索引。

    PostgreSQL 是 RAG 事实来源；关键词检索直接查询 ``KnowledgeChunk``，
    Qdrant 只保存可删除、可重建的向量索引。恢复只在 chunk 缺失、内容与事实
    来源不一致或 Qdrant 中该文档的向量数量不一致时执行，避免每次启动重复计算 embedding。
    """
    document_rows = (await db.execute(
        select(KnowledgeDocument, KnowledgeBase)
        .join(KnowledgeBase, KnowledgeBase.id == KnowledgeDocument.kb_id)
        .order_by(KnowledgeDocument.id)
    )).all()
    repaired = 0
    for document, knowledge_base in document_rows:
        # 早期手工创建的文档可能没有 source_type；按普通上传文档处理，
        # 否则启动修复会跳过它并留下损坏的切片。
        source_type = document.source_type or "upload"
        if source_type not in BUSINESS_SOURCE_TYPES:
            # 原始物料不进入默认 RAG；代码和 OMD 由专用入口/工具读取。
            continue
        chunks = _chunks(document.content or "")
        if not chunks:
            continue

        existing_chunks = (await db.execute(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document.id)
            .order_by(KnowledgeChunk.chunk_index)
        )).scalars().all()
        expected_ids = [f"{document.id}#c{i}" for i in range(len(chunks))]
        if ([item.id for item in existing_chunks] != expected_ids
                or [item.content for item in existing_chunks] != chunks):
            # chunk 是文档 content 的确定性派生物。部分/旧切片必须整体重建，
            # 否则仅按数量判断会把损坏的旧内容当成有效索引。
            if existing_chunks:
                await db.execute(
                    sa_delete(KnowledgeChunk).where(
                        KnowledgeChunk.document_id == document.id
                    )
                )
            db.add_all([
                KnowledgeChunk(
                    id=f"{document.id}#c{i}",
                    kb_id=document.kb_id,
                    document_id=document.id,
                    chunk_index=i,
                    content=chunk,
                )
                for i, chunk in enumerate(chunks)
            ])
            document.chunk_count = len(chunks)
            repaired += 1
        elif document.chunk_count != len(chunks):
            document.chunk_count = len(chunks)

        # 只在 embedding 已配置且 Qdrant 可访问时尝试恢复向量；服务暂时不可用
        # 不阻塞启动，文档仍可通过 PostgreSQL 关键词检索。
        if not _embedding_configured():
            continue
        vector_count = await asyncio.to_thread(
            _qdrant_document_count, knowledge_base.collection_name, document.id,
        )
        if vector_count is None:
            continue
        if vector_count == len(chunks) and document.status == "indexed":
            continue
        try:
            vector_indexed = await _index_qdrant(knowledge_base, document, chunks)
            if vector_indexed:
                document.status = "indexed"
                document.error_message = ""
            else:
                document.status = "indexed_keyword"
        except Exception as exc:  # noqa: BLE001
            document.status = "indexed_keyword"
            document.error_message = f"向量索引恢复失败，已保留关键词检索：{str(exc)[:300]}"

    await db.flush()
    return repaired


def _embedding_configured() -> bool:
    """延迟导入核心 RAG 配置，避免平台启动时提前加载可选依赖。"""
    from datadeck.agents.toolkits.rag_store import embedding_configured

    return embedding_configured()


def _qdrant_document_count(collection_name: str, document_id: str) -> int | None:
    """返回一个文档在 Qdrant 中的向量数；不可用时返回 None。

    同时匹配新旧 payload 字段，便于一次性重建旧 collection 中的点。
    """
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        client = QdrantClient(
            url=os.getenv("DATADECK_QDRANT_URL", "http://localhost:6333"),
            timeout=10,
            trust_env=False,
        )
        if not client.collection_exists(collection_name):
            return 0
        result = client.count(
            collection_name=collection_name,
            count_filter=Filter(should=[
                FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                FieldCondition(key="doc_id", match=MatchValue(value=document_id)),
            ]),
            exact=True,
        )
        return int(result.count)
    except Exception:
        return None


async def list_documents(db: AsyncSession, kb_id: str, uid: str) -> list[dict]:
    await get_knowledge_base(db, uid, kb_id)
    items = (await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id,
                                        KnowledgeDocument.source_type.in_(BUSINESS_SOURCE_TYPES))
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

    client = QdrantClient(url=os.getenv("DATADECK_QDRANT_URL", "http://localhost:6333"), timeout=10, trust_env=False)
    if not client.collection_exists(kb.collection_name):
        client.create_collection(
            collection_name=kb.collection_name,
            vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE),
        )
    # 同一文档重建时先删除旧分块，避免旧版本较长的尾部 chunk 残留在向量库。
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    client.delete(
        collection_name=kb.collection_name,
        points_selector=Filter(should=[
            FieldCondition(key="document_id", match=MatchValue(value=document.id)),
            FieldCondition(key="doc_id", match=MatchValue(value=document.id)),
        ]),
    )
    client.upsert(
        collection_name=kb.collection_name,
        points=[PointStruct(
            # Qdrant point id 只能使用整数或 UUID，数据库 chunk id 仍保留
            # ``<document_id>#c<index>`` 作为业务标识写入 payload。
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"datadeck:{document.id}#c{i}"), vector=vector,
            payload={"chunk_id": f"{document.id}#c{i}", "document_id": document.id, "doc_id": document.id,
                     "filename": document.filename, "title": document.filename,
                     "content": chunk, "source_type": document.source_type,
                     "source_id": document.source_id, "layer": document.layer,
                     "version": document.version, "metadata": document.metadata_json or {}},
        ) for i, (vector, chunk) in enumerate(zip(vectors, chunks, strict=True))],
    )
    return True


async def add_document(
    db: AsyncSession, uid: str, kb_id: str, filename: str, content: bytes,
    *, source_type: str = "upload", source_id: str | None = None,
    layer: str = "hot", metadata: dict | None = None, version: str = "1",
) -> KnowledgeDocument:
    kb = await get_knowledge_base(db, uid, kb_id)
    filename = _safe_filename(filename)
    text = _decode(filename, content)
    chunks = _chunks(text)
    if not chunks:
        raise ValueError("文档内容不能为空")
    document = KnowledgeDocument(
        id=str(uuid.uuid4()), kb_id=kb.id, filename=filename, content=text,
        source_type=source_type, source_id=source_id or "", layer=layer,
        content_hash=hashlib.sha256(content).hexdigest(), metadata_json=metadata or {},
        version=version,
        status="indexing", chunk_count=len(chunks),
    )
    if not document.source_id:
        document.source_id = document.id
    db.add(document)
    await db.flush()
    await db.execute(sa_delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))
    db.add_all([
        KnowledgeChunk(
            id=f"{document.id}#c{i}", kb_id=kb.id, document_id=document.id,
            chunk_index=i, content=chunk,
        )
        for i, chunk in enumerate(chunks)
    ])
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
        client = QdrantClient(url=os.getenv("DATADECK_QDRANT_URL", "http://localhost:6333"), timeout=10, trust_env=False)
        if client.collection_exists(kb.collection_name):
            client.delete(
                collection_name=kb.collection_name,
                points_selector=Filter(should=[
                    FieldCondition(key="document_id", match=MatchValue(value=document.id)),
                    FieldCondition(key="doc_id", match=MatchValue(value=document.id)),
                ]),
            )
    except Exception:
        pass
    await db.delete(document)


async def delete_knowledge_base(db: AsyncSession, uid: str, kb_id: str) -> None:
    kb = await get_knowledge_base(db, uid, kb_id)
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=os.getenv("DATADECK_QDRANT_URL", "http://localhost:6333"), timeout=10, trust_env=False)
        if client.collection_exists(kb.collection_name):
            client.delete_collection(kb.collection_name)
    except Exception:
        pass
    await db.delete(kb)


async def search(
    db: AsyncSession,
    uid: str,
    kb_id: str,
    query: str,
    top_k: int = 5,
    *,
    authorized_kb_ids: set[str] | None = None,
) -> dict:
    kb = await get_knowledge_base(db, uid, kb_id, authorized_ids=authorized_kb_ids)
    query = query.strip()
    if not query:
        raise ValueError("检索内容不能为空")
    current_chunk_ids = {
        str(item) for item in (await db.execute(
            select(KnowledgeChunk.id).join(
                KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id,
            ).where(KnowledgeChunk.kb_id == kb.id,
                    KnowledgeDocument.source_type.in_(BUSINESS_SOURCE_TYPES))
        )).scalars().all()
    }
    from datadeck.agents.toolkits.rag_store import embed_texts, embedding_configured
    if embedding_configured():
        try:
            from qdrant_client import QdrantClient
            client = QdrantClient(url=os.getenv("DATADECK_QDRANT_URL", "http://localhost:6333"), timeout=10, trust_env=False)
            if client.collection_exists(kb.collection_name):
                vectors = embed_texts([query])
                if vectors:
                    points = client.query_points(
                        collection_name=kb.collection_name, query=vectors[0], limit=top_k
                    ).points
                    points = [point for point in points if str(
                        (point.payload or {}).get("chunk_id") or point.id
                    ) in current_chunk_ids]
                    if points:
                        return {"results": [
                            {"document_id": p.payload.get("document_id"),
                             "chunk_id": p.payload.get("chunk_id", str(p.id)),
                             "filename": p.payload.get("filename"),
                             "content": p.payload.get("content", ""),
                             "source_type": p.payload.get("source_type", "upload"),
                             "source_id": p.payload.get("source_id", p.payload.get("document_id", "")),
                             "layer": p.payload.get("layer", "hot"),
                             "version": p.payload.get("version", "1"),
                             "metadata": p.payload.get("metadata", {}),
                             "score": round(float(p.score), 4)}
                            for p in points
                        ], "strategy": "qdrant"}
        except Exception:
            pass
    terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query)]
    chunks = (await db.execute(
        select(KnowledgeChunk, KnowledgeDocument.filename, KnowledgeDocument.source_type,
               KnowledgeDocument.source_id, KnowledgeDocument.layer, KnowledgeDocument.version,
               KnowledgeDocument.metadata_json)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(KnowledgeChunk.kb_id == kb.id,
               KnowledgeDocument.source_type.in_(BUSINESS_SOURCE_TYPES))
        .order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
    )).all()
    if chunks:
        ranked_chunks = sorted(
            ((sum(text.count(term) for term in terms), chunk, filename,
              source_type, source_id, layer, version, metadata_json)
             for chunk, filename, source_type, source_id, layer, version, metadata_json in chunks
             for text in [chunk.content.lower()]),
            key=lambda item: item[0], reverse=True,
        )
        return {"results": [
             {"document_id": chunk.document_id, "chunk_id": chunk.id,
             "filename": filename, "content": chunk.content[:1200], "score": score,
             "source_type": source_type, "source_id": source_id,
             "layer": layer, "version": version, "metadata": metadata_json or {}}
            for score, chunk, filename, source_type, source_id, layer, version, metadata_json
            in ranked_chunks[:top_k] if score > 0
        ], "strategy": "keyword"}

    return {"results": [], "strategy": "empty"}
