"""盘点并迁移历史混合知识物料。

默认只输出报告；传入 ``--apply`` 后会把代码、OMD 快照、OSSIE 原始记录和
wiki_extract 标记为 archived，并删除它们的 PostgreSQL 派生 chunk。原始文档
仍保留在公共知识物料路径中，业务知识文档不会被修改。脚本可重复执行。
"""
from __future__ import annotations

# 脚本入口在插入项目根目录后再加载宿主服务模块。
# ruff: noqa: E402
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, func, select, update

from server.db import async_session_factory
from server.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from server.services.knowledge_service import BUSINESS_SOURCE_TYPES

RAW_SOURCE_TYPES = {"code", "omd", "ossie", "wiki_extract"}


async def migrate(apply: bool) -> dict:
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(
                KnowledgeDocument.source_type,
                KnowledgeDocument.status,
                func.count(KnowledgeDocument.id),
            )
            .where(KnowledgeDocument.source_type.not_in(BUSINESS_SOURCE_TYPES))
            .group_by(KnowledgeDocument.source_type, KnowledgeDocument.status)
            .order_by(KnowledgeDocument.source_type, KnowledgeDocument.status)
        )).all()
        report = {
            "business_source_types": sorted(BUSINESS_SOURCE_TYPES),
            "raw_source_types": sorted(RAW_SOURCE_TYPES),
            "groups": [
                {"source_type": source_type, "status": status, "documents": count}
                for source_type, status, count in rows
            ],
            "applied": apply,
            "archived_documents": 0,
            "deleted_chunks": 0,
            "qdrant_collections_cleaned": 0,
        }
        if apply:
            raw_docs = (await db.execute(
                select(KnowledgeDocument.id, KnowledgeDocument.kb_id).where(
                    KnowledgeDocument.source_type.in_(RAW_SOURCE_TYPES),
                    KnowledgeDocument.status != "archived",
                )
            )).all()
            if raw_docs:
                ids = [item.id for item in raw_docs]
                chunk_result = await db.execute(
                    delete(KnowledgeChunk).where(KnowledgeChunk.document_id.in_(ids))
                )
                archived_result = await db.execute(
                    update(KnowledgeDocument)
                    .where(KnowledgeDocument.id.in_(ids))
                    .values(status="archived", error_message="原始物料仅保留在公共知识物料区，不进入默认 RAG")
                )
                report["archived_documents"] = int(archived_result.rowcount or 0)
                report["deleted_chunks"] = int(chunk_result.rowcount or 0)
                # Qdrant 仅是派生索引；清理失败不回滚 PostgreSQL 归档，下一次
                # 脚本仍可重复尝试，且 RAG 查询已有 source_type 防线。
                try:
                    from qdrant_client import QdrantClient
                    from qdrant_client.models import FieldCondition, Filter, MatchAny
                    kb_ids = {item.kb_id for item in raw_docs}
                    kbs = (await db.execute(select(KnowledgeBase).where(KnowledgeBase.id.in_(kb_ids)))).scalars().all()
                    client = QdrantClient(
                        url=os.getenv("DATADECK_QDRANT_URL", "http://127.0.0.1:6333"),
                        timeout=10, trust_env=False,
                    )
                    for kb in kbs:
                        if not client.collection_exists(kb.collection_name):
                            continue
                        document_ids = [item.id for item in raw_docs if item.kb_id == kb.id]
                        client.delete(collection_name=kb.collection_name, points_selector=Filter(should=[
                            FieldCondition(key="document_id", match=MatchAny(any=document_ids)),
                            FieldCondition(key="doc_id", match=MatchAny(any=document_ids)),
                        ]))
                        report["qdrant_collections_cleaned"] += 1
                except Exception:
                    pass
            await db.commit()
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移历史混合知识物料，默认仅生成报告")
    parser.add_argument("--apply", action="store_true", help="归档原始物料并删除其 PostgreSQL chunk")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(migrate(args.apply)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
