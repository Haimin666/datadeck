"""RAG 检索层：Qdrant 向量库 + SiliconFlow BGE-M3 embedding + Reranker + BM25 混合（RRF）。

env 配置：
- DATADECK_QDRANT_URL:  默认 http://localhost:6333
- DATADECK_EMBEDDING_API_URL: 默认 https://api.siliconflow.cn/v1/embeddings
- DATADECK_EMBEDDING_API_KEY: SiliconFlow key
- DATADECK_EMBEDDING_MODEL:   默认 BAAI/bge-m3
- DATADECK_RERANK_API_URL:    默认 https://api.siliconflow.cn/v1/rerank
- DATADECK_RERANK_MODEL:      默认 BAAI/bge-reranker-v2-m3
- DATADECK_RAG_COLLECTION:    默认 datadeck_kb

检索策略：Qdrant 向量召回 + BM25 关键词召回 → RRF 融合 → Rerank 重排（可选）。
未配置 embedding key 时降级纯 BM25（本地，零依赖）。
"""

from __future__ import annotations

import math
import os
import re
import uuid
from threading import RLock
from typing import Any

from datadeck import logger

COLLECTION = os.getenv("DATADECK_RAG_COLLECTION", "datadeck_kb")
VECTOR_SIZE = 1024  # BGE-M3
QDRANT_URL = os.getenv("DATADECK_QDRANT_URL", "http://localhost:6333")

# ── 文档切片 ──────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 80) -> list[str]:
    """中文友好切片：按段落聚合到 ~chunk_size 字符，相邻块重叠 overlap。"""
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size 必须大于 0，overlap 必须位于 [0, chunk_size) 内")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for para in paragraphs:
        # 超长段落硬切；每个后续片段保留前一片段的尾部 overlap。
        while len(para) > chunk_size:
            chunks.append(para[:chunk_size])
            para = para[chunk_size - overlap:]
        candidate = f"{buffer}\n{para}".strip() if buffer else para
        if len(candidate) <= chunk_size:
            buffer = candidate
        else:
            if buffer:
                chunks.append(buffer)
                buffer = f"{buffer[-overlap:]}\n{para}".strip() if overlap else para
            else:
                buffer = para
            if len(buffer) > chunk_size:
                chunks.append(buffer[:chunk_size])
                buffer = buffer[chunk_size - overlap:]
    if buffer:
        chunks.append(buffer)
    return chunks


# ── BM25（本地，无外部依赖） ───────────────────────────────

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+")

def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(str(text).lower())


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self._docs: list[dict] = []

    def add(self, doc_id: str, text: str) -> None:
        self._docs.append({"id": doc_id, "terms": tokenize(text)})

    def build(self) -> "BM25Index":
        self._df: dict[str, int] = {}
        for d in self._docs:
            for t in set(d["terms"]):
                self._df[t] = self._df.get(t, 0) + 1
        self._avg_len = (
            sum(len(d["terms"]) for d in self._docs) / len(self._docs) if self._docs else 1
        )
        return self

    def search(self, query: str, top_k: int = 10,
               allowed_ids: set[str] | None = None) -> list[tuple[str, float]]:
        q_terms = tokenize(query)
        scores: dict[str, float] = {}
        N = len(self._docs)
        for d in self._docs:
            if allowed_ids is not None and d["id"] not in allowed_ids:
                continue
            tf: dict[str, int] = {}
            for t in d["terms"]:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            dl = len(d["terms"])
            for t in q_terms:
                if t not in tf or t not in self._df:
                    continue
                idf = math.log(1 + (N - self._df[t] + 0.5) / (self._df[t] + 0.5))
                s += idf * (tf[t] * (self.k1 + 1)) / (
                    tf[t] + self.k1 * (1 - self.b + self.b * dl / self._avg_len)
                )
            if s > 0:
                scores[d["id"]] = s
        return sorted(scores.items(), key=lambda x: -x[1])[:top_k]


# ── Embedding / Rerank（SiliconFlow OpenAI 兼容） ──────────

def embedding_configured() -> bool:
    return bool(os.getenv("DATADECK_EMBEDDING_API_KEY"))


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    if not embedding_configured():
        return None
    import json
    import urllib.request

    url = os.getenv(
        "DATADECK_EMBEDDING_API_URL",
        "https://api.siliconflow.cn/v1/embeddings",
    )
    model = os.getenv("DATADECK_EMBEDDING_MODEL", "BAAI/bge-m3")
    body = json.dumps({"input": texts, "model": model}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {os.getenv('DATADECK_EMBEDDING_API_KEY')}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
    except Exception as exc:  # noqa: BLE001
        logger.error(f"embedding 调用失败: {str(exc)[:200]}")
        return None


def rerank(query: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """SiliconFlow rerank；失败/未配置返回 None（调用方用 RRF 结果）。"""
    if not os.getenv("DATADECK_EMBEDDING_API_KEY") or not docs:
        return None
    import json
    import urllib.request

    url = os.getenv(
        "DATADECK_RERANK_API_URL",
        "https://api.siliconflow.cn/v1/rerank",
    )
    model = os.getenv("DATADECK_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
    body = json.dumps({
        "model": model,
        "query": query,
        "documents": [d["content"] for d in docs],
    }).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {os.getenv('DATADECK_EMBEDDING_API_KEY')}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])
        reranked = []
        for r in sorted(results, key=lambda x: -x.get("relevance_score", 0)):
            doc = docs[r["index"]].copy()
            doc["score"] = r.get("relevance_score", 0)
            reranked.append(doc)
        return reranked
    except Exception as exc:  # noqa: BLE001
        logger.error(f"rerank 调用失败: {str(exc)[:200]}")
        return None


# ── Qdrant 存取 ───────────────────────────────────────────

def _qdrant_client():
    from qdrant_client import QdrantClient

    return QdrantClient(url=QDRANT_URL, timeout=10, trust_env=False)


def ensure_collection(collection_name: str | None = None) -> bool:
    if not embedding_configured():
        return False
    try:
        client = _qdrant_client()
        collection_name = collection_name or COLLECTION
        if not client.collection_exists(collection_name):
            from qdrant_client.models import Distance, VectorParams

            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Qdrant 不可用: {str(exc)[:200]}")
        return False


def _collection_name(collection_name: str | None = None) -> str:
    return str(collection_name or COLLECTION)


def _store_key(collection_name: str, chunk_id: str) -> str:
    return f"{collection_name}:{chunk_id}"


def _qdrant_point_id(doc_id: str, chunk_index: int) -> uuid.UUID:
    """将业务 chunk id 转成 Qdrant 接受的稳定 UUID。"""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"datadeck:{doc_id}#c{chunk_index}")


def _remove_bm25_document(collection_name: str, doc_id: str) -> int:
    with _bm25_lock:
        removed = [
            key for key, chunk in _chunk_store.items()
            if key.startswith(f"{collection_name}:") and chunk.get("doc_id") == doc_id
        ]
        for key in removed:
            _chunk_store.pop(key, None)
        if removed:
            _bm25_cache.pop(f"{collection_name}:all", None)
        return len(removed)


def _delete_qdrant_document(collection_name: str, doc_id: str) -> bool | None:
    if not embedding_configured() or not ensure_collection(collection_name):
        return None
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        _qdrant_client().delete(
            collection_name=collection_name,
            points_selector=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            ),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Qdrant 删除失败: {str(exc)[:200]}")
        return False


def add_document(
    doc_id: str, title: str, content: str, metadata: dict | None = None,
    collection_name: str | None = None,
) -> dict[str, Any]:
    """文档入库：切片 → embedding → Qdrant upsert + BM25 内存索引重建。"""
    collection_name = _collection_name(collection_name)
    chunks = chunk_text(content)
    if not chunks:
        return {"ok": False, "error": "文档内容为空"}

    use_vector = embedding_configured() and ensure_collection(collection_name)
    points: list = []
    vectors = embed_texts(chunks) if use_vector else None

    if use_vector and vectors:
        from qdrant_client.models import PointStruct

        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            points.append(PointStruct(
                id=_qdrant_point_id(doc_id, i),
                vector=vec,
                payload={
                    "chunk_id": f"{doc_id}#c{i}", "doc_id": doc_id, "chunk_index": i, "title": title,
                    "content": chunk, **(metadata or {}),
                },
            ))
        _delete_qdrant_document(collection_name, doc_id)
        _qdrant_client().upsert(collection_name=collection_name, points=points)
    elif use_vector:
        # embedding 失败时也移除旧版本，避免返回已经过期的向量结果。
        _delete_qdrant_document(collection_name, doc_id)

    _remove_bm25_document(collection_name, doc_id)
    for i, chunk in enumerate(chunks):
        _register_chunk(collection_name, f"{doc_id}#c{i}", {
            "doc_id": doc_id, "chunk_index": i, "title": title, "content": chunk,
            **(metadata or {}),
        })

    return {
        "ok": True,
        "doc_id": doc_id,
        "title": title,
        "chunk_count": len(chunks),
        "vector_indexed": bool(use_vector and vectors),
        "note": "" if use_vector else "（embedding 未配置或 Qdrant 不可用，仅 BM25 可检索）",
    }


def delete_document(doc_id: str, collection_name: str | None = None) -> dict[str, Any]:
    """增量删除：Qdrant 按 payload.doc_id 过滤删除 + BM25 摘除。"""
    collection_name = _collection_name(collection_name)
    removed_bm25 = _remove_bm25_document(collection_name, doc_id)
    qdrant_ok = _delete_qdrant_document(collection_name, doc_id)

    return {
        "ok": True, "doc_id": doc_id,
        "bm25_removed": removed_bm25, "qdrant_deleted": qdrant_ok,
    }


def _qdrant_search(query: str, top_k: int = 10, domain: str | None = None,
                   collection_name: str | None = None) -> list[dict[str, Any]]:
    try:
        qvec = embed_texts([query])
        if not qvec:
            return []
        query_filter = None
        if domain:
            from qdrant_client.models import FieldCondition, Filter, MatchValue

            query_filter = Filter(
                must=[FieldCondition(key="domain", match=MatchValue(value=domain))]
            )
        res = _qdrant_client().query_points(
            collection_name=_collection_name(collection_name), query=qvec[0], limit=top_k,
            query_filter=query_filter,
        )
        return [
            {
                "id": str(p.payload.get("chunk_id", p.id)),
                "chunk_id": str(p.payload.get("chunk_id", p.id)),
                "title": p.payload.get("title", ""),
                "filename": p.payload.get("filename", p.payload.get("title", "")),
                "content": p.payload.get("content", ""),
                "doc_id": p.payload.get("doc_id", ""),
                "score": p.score,
            }
            for p in res.points
        ]
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Qdrant 检索失败: {str(exc)[:200]}")
        return []


def _rrf_fuse(vector_hits: list[dict], bm25_hits: list[tuple[str, dict]], k: int = 60, top_k: int = 10):
    """RRF: score = Σ 1/(k+rank)。vector_hits 有 score；bm25_hits 是 (id, doc)。"""
    pooled: dict[str, dict] = {}
    for rank, h in enumerate(vector_hits):
        key = h["id"]
        entry = pooled.setdefault(key, {**h, "rrf": 0.0})
        entry["rrf"] += 1.0 / (k + rank + 1)
    for rank, (cid, doc) in enumerate(bm25_hits):
        entry = pooled.setdefault(cid, {**doc, "id": cid, "rrf": 0.0})
        entry["rrf"] += 1.0 / (k + rank + 1)
    fused = sorted(pooled.values(), key=lambda x: -x["rrf"])
    return fused[:top_k]


# standalone 模式的本地索引。平台运行时不使用这里的文档状态，宿主会把
# rag_search 注入到 PostgreSQL 知识库查询。
_bm25_cache: dict[str, BM25Index] = {}
_bm25_lock = RLock()
BM25_CACHE_MAX_COLLECTIONS = 64


def _bm25_search(query: str, top_k: int = 10, domain: str | None = None,
                 collection_name: str | None = None) -> list[tuple[str, dict]]:
    collection_name = _collection_name(collection_name)
    with _bm25_lock:
        allowed = {
            key for key, payload in _chunk_store.items()
            if key.startswith(f"{collection_name}:")
            and (not domain or payload.get("domain") == domain)
        }
    hits = _bm25_all_index(collection_name).search(query, top_k, allowed_ids=allowed)
    with _bm25_lock:
        prefix = f"{collection_name}:"
        return [
            (cid[len(prefix):], dict(_chunk_store[cid]))
            for cid, _ in hits if cid in _chunk_store and cid.startswith(prefix)
        ]


_chunk_store: dict[str, dict] = {}


def _bm25_all_index(collection_name: str | None = None) -> BM25Index:
    collection_name = _collection_name(collection_name)
    key = f"{collection_name}:all"
    with _bm25_lock:
        if key in _bm25_cache:
            return _bm25_cache[key]
        index = BM25Index()
        for cid, payload in _chunk_store.items():
            if not cid.startswith(f"{collection_name}:"):
                continue
            title = payload.get("title", "")
            index.add(cid, f"{title}\n{payload.get('content', '')}")
        _bm25_cache[key] = index.build()
        # 只淘汰可按需重建的 BM25 对象；standalone 文档数据由调用方显式维护。
        if len(_bm25_cache) > BM25_CACHE_MAX_COLLECTIONS:
            _bm25_cache.pop(next(iter(_bm25_cache)))
        return _bm25_cache[key]


def _register_chunk(collection_name: str, chunk_id: str, payload: dict) -> None:
    """add_document 时同步登记 BM25 索引（进程内）。"""
    with _bm25_lock:
        _chunk_store[_store_key(collection_name, chunk_id)] = dict(payload)
        _bm25_cache.pop(f"{collection_name}:all", None)


def clear_bm25_collection(collection_name: str | None = None) -> int:
    """清空一个知识库的 BM25 缓存，供启动重建或热重建使用。"""
    prefix = f"{_collection_name(collection_name)}:"
    with _bm25_lock:
        removed = [key for key in _chunk_store if key.startswith(prefix)]
        for key in removed:
            _chunk_store.pop(key, None)
        _bm25_cache.pop(f"{prefix[:-1]}:all", None)
        return len(removed)


def register_document_chunks(collection_name: str, doc_id: str, title: str,
                             chunks: list[str], metadata: dict | None = None) -> None:
    """供宿主持久化知识库同步关键词索引；不依赖平台数据库。"""
    _remove_bm25_document(collection_name, doc_id)
    for i, chunk in enumerate(chunks):
        _register_chunk(collection_name, f"{doc_id}#c{i}", {
            "doc_id": doc_id, "chunk_index": i, "title": title,
            "content": chunk, **(metadata or {}),
        })


def search(query: str, top_k: int = 5, domain: str | None = None,
           collection_name: str | None = None) -> dict[str, Any]:
    """混合检索：向量 + BM25 → RRF → rerank（可选）；domain 业务域过滤（可选）。"""
    collection_name = _collection_name(collection_name)
    top_k = min(max(int(top_k), 1), 20)
    with _bm25_lock:
        has_keyword_docs = any(k.startswith(f"{collection_name}:") for k in _chunk_store)
    if not has_keyword_docs and not embedding_configured():
        return {
            "ok": True, "results": [], "strategy": "empty",
            "note": "知识库为空且未配置 embedding，请先在知识库模块录入文本物料。",
        }

    vector_hits = (
        _qdrant_search(query, top_k=max(top_k * 2, 10), domain=domain,
                       collection_name=collection_name)
        if embedding_configured() else []
    )
    bm25_hits = _bm25_search(query, top_k=max(top_k * 2, 10), domain=domain,
                             collection_name=collection_name)
    fused = _rrf_fuse(vector_hits, bm25_hits, top_k=top_k * 2)

    if not fused:
        return {"ok": True, "results": [], "strategy": "no_hit", "note": "未检索到相关内容"}

    strategy = "hybrid_rrf"
    reranked = rerank(query, fused)
    if reranked is not None:
        fused = reranked
        strategy = "hybrid_rrf_rerank"
    elif not vector_hits and bm25_hits:
        strategy = "bm25_only"

    results = [
        {
            "title": h.get("title", ""),
            "filename": h.get("filename", h.get("title", "")),
            "content": h.get("content", ""),
            "doc_id": h.get("doc_id", ""),
            "chunk_id": h.get("chunk_id", h.get("id", "")),
            "score": round(float(h.get("score") or h.get("rrf") or 0), 4),
        }
        for h in fused[:top_k]
    ]
    return {"ok": True, "results": results, "strategy": strategy, "count": len(results)}


def test_connection() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    if embedding_configured():
        vec = embed_texts(["ping"])
        checks["embedding"] = {"ok": bool(vec), "model": os.getenv("DATADECK_EMBEDDING_MODEL", "BAAI/bge-m3")}
        checks["qdrant"] = {"ok": ensure_collection(), "url": QDRANT_URL}
    else:
        checks["embedding"] = {"ok": False, "note": "未配置 DATADECK_EMBEDDING_API_KEY，仅 BM25 可用"}
        checks["qdrant"] = {"ok": False, "note": "跳过"}
    return {"ok": all(v.get("ok") for v in checks.values()), "checks": checks}
