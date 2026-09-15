"""将 data_material 当前交付物幂等导入 DataDeck PostgreSQL。"""
from __future__ import annotations

# 项目根路径必须在宿主模块导入前加入 sys.path。
# ruff: noqa: E402

import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from sqlalchemy import select

from server.db import async_session_factory
from server.models import KnowledgeBase, KnowledgeDocument, User
from server.services.knowledge_service import add_document, delete_document
from server.services.metric_registry import MetricRegistry

MATERIAL_ROOT = ROOT / "data_material"
BUSINESS_SOURCE_TYPES = {"upload", "wiki", "business_doc"}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _material(source_type: str, source_id: str, title: str, content, *, layer="cold", metadata=None):
    text = content if isinstance(content, str) else _json(content)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = hashlib.sha256(f"{source_type}:{source_id}".encode()).hexdigest()[:64]
    return {
        "id": key, "source_type": source_type, "source_id": source_id[:512],
        "title": title[:512], "content": text, "metadata": metadata or {},
        "layer": layer, "content_hash": digest,
    }


def _knowledge_filename(material: dict) -> str:
    """为无扩展名的整理物料补上受支持的文本扩展名。"""
    title = str(material.get("title") or material.get("source_id") or "document").strip()
    if Path(title).suffix.lower() in {".txt", ".md", ".markdown", ".csv", ".json"}:
        return title
    extension = ".md" if material.get("source_type") in {"wiki", "business_doc"} else ".json"
    return f"{title}{extension}"


def collect_materials():
    records = []
    manifest_path = MATERIAL_ROOT / "wiki/70331927/manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for page in manifest.get("pages", []):
            path = ROOT / page["path"]
            if path.exists():
                records.append(_material("wiki", page["id"], page["title"],
                                         path.read_text(encoding="utf-8"), metadata=page))
    for path in sorted((MATERIAL_ROOT / "delivery/business_docs").glob("*.md")):
        records.append(_material("business_doc", path.name, path.stem,
                                 path.read_text(encoding="utf-8"), metadata={"path": str(path)}))
    for source_type, path in (("code", MATERIAL_ROOT / "lion_dw/code_inventory.jsonl"),
                              ("omd", MATERIAL_ROOT / "omd/table_metadata.jsonl"),
                              ("ossie", MATERIAL_ROOT / "delivery/metrics/metric_review.jsonl"),
                              ("wiki_extract", MATERIAL_ROOT / "delivery/wiki_keyword_extract.jsonl")):
        if not path.exists():
            continue
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            value = json.loads(line)
            source_id = value.get("fqn") or value.get("path") or value.get("metric_name") or str(index)
            title = value.get("name") or value.get("display_name") or value.get("title") or source_id
            layer = "hot" if source_type == "omd" and value.get("status") == "ok" else "cold"
            records.append(_material(source_type, str(source_id), str(title), value,
                                     layer=layer, metadata={"source_file": str(path.relative_to(ROOT))}))
    return records


def _metric_status(review_item: dict, ai_context: dict, *, conflict: bool) -> tuple[str, dict]:
    """把 OSSIE 审核状态映射为数据库状态，避免冲突的双重事实来源。"""
    raw_status = review_item.get("status") or ai_context.get("review_status") or "candidate"
    status = str(raw_status).strip()
    if status not in {"candidate", "needs_review", "approved", "rejected"}:
        status = "needs_review"
    # 代码抽取发现多个口径时，无论原始记录如何都必须回到人工审核态。
    if conflict:
        status = "needs_review"
    normalized_context = dict(ai_context)
    normalized_context["review_status"] = status
    return status, normalized_context


def collect_metrics():
    path = MATERIAL_ROOT / "delivery/metrics/semantic_model.yaml"
    review_path = MATERIAL_ROOT / "delivery/metrics/metric_review.jsonl"
    review = {x["metric_name"]: x for x in
              (json.loads(line) for line in review_path.read_text(encoding="utf-8").splitlines() if line.strip())}
    model = yaml.safe_load(path.read_text(encoding="utf-8"))
    semantic = model.get("semantic_model") or {}
    metrics = semantic.get("metrics", []) if isinstance(semantic, dict) else (
        semantic[0].get("metrics", []) if semantic and isinstance(semantic[0], dict) else [])
    for metric in metrics:
        review_item = review.get(metric["name"], {})
        evidence = review_item.get("evidence", [])
        conflict = bool(review_item.get("conflict"))
        status, ai_context = _metric_status(review_item, metric.get("ai_context") or {}, conflict=conflict)
        yield MetricRegistry(canonical_name=metric.get("description", metric["name"]).replace("（代码候选，待业务确认）", "").strip(),
                             aliases=ai_context.get("synonyms", []),
                             definition=metric.get("description", ""),
                             formula=(evidence[0].get("expression") if evidence else None),
                             ossie_name=metric["name"], ossie_expression=metric.get("expression"),
                             datatype=metric.get("datatype"), ai_context=ai_context,
                             source_evidence=evidence, status=status,
                             conflict_status="conflict" if conflict else "none",
                             conflict_details=evidence if conflict else [])


def collect_metric_candidates():
    """保留代码扫描得到的每一条原始候选，不让去重后的语义模型吞掉证据。"""
    path = MATERIAL_ROOT / "lion_dw/metric_candidates.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _candidate_metric_name(candidate: dict, used_names: set[str]) -> str:
    base = str(candidate.get("comment") or candidate.get("name") or "未命名指标").strip()[:128]
    if base not in used_names:
        used_names.add(base)
        return base
    suffix = hashlib.sha256(str(candidate.get("candidate_id", "")).encode()).hexdigest()[:10]
    name = f"{base[:117]} [{suffix}]"
    used_names.add(name)
    return name


def build_candidate_metrics(candidates, existing_items):
    used_names = {str(item.canonical_name) for item in existing_items if item.canonical_name}
    used_ossie_names = {str(item.ossie_name) for item in existing_items if item.ossie_name}
    known_ids = {
        str(evidence.get("candidate_id"))
        for item in existing_items
        for evidence in (item.source_evidence or [])
        if isinstance(evidence, dict) and evidence.get("candidate_id")
    }
    result = []
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id or candidate_id in known_ids:
            continue
        evidence = [{
            "candidate_id": candidate_id,
            "path": candidate.get("source_path", ""),
            "line": candidate.get("source_line"),
            "comment": candidate.get("comment", ""),
            "aggregation": candidate.get("aggregation", ""),
        }]
        ossie_name = str(candidate.get("name") or candidate_id)[:160]
        if ossie_name in used_ossie_names:
            suffix = hashlib.sha256(candidate_id.encode()).hexdigest()[:10]
            ossie_name = f"{ossie_name[:149]}#{suffix}"
        used_ossie_names.add(ossie_name)
        result.append(MetricRegistry(
            canonical_name=_candidate_metric_name(candidate, used_names),
            aliases=[],
            definition=str(candidate.get("comment") or candidate.get("name") or "代码候选指标"),
            formula=str(candidate.get("aggregation") or "") or None,
            ossie_name=ossie_name,
            ossie_expression={"aggregation": candidate.get("aggregation"), "source": candidate.get("name")},
            ai_context={"source": "lion_dw.metric_candidates", "candidate": True,
                        "review_status": "candidate"},
            source_evidence=evidence,
            status="candidate",
            conflict_status="none",
            conflict_details=[],
        ))
    return result


async def main():
    materials = collect_materials()
    metrics = list(collect_metrics())
    candidate_metrics = []
    async with async_session_factory() as db:
        existing_metrics = list((await db.execute(select(MetricRegistry))).scalars().all())
        # 语义指标尚未 flush 到数据库，生成代码候选时也要提前占用它们的
        # canonical_name，避免同一批导入互相触发唯一键冲突。
        candidate_metrics = build_candidate_metrics(
            collect_metric_candidates(), [*existing_metrics, *metrics])
        for item in candidate_metrics:
            db.add(item)
        for item in metrics:
            existing = await db.scalar(select(MetricRegistry).where(MetricRegistry.ossie_name == item.ossie_name))
            if existing:
                for key in ("canonical_name", "aliases", "definition", "formula", "ossie_expression", "datatype", "ai_context", "source_evidence", "status", "conflict_status", "conflict_details", "updated_at"):
                    setattr(existing, key, getattr(item, key))
            else:
                db.add(item)
        # 全部 RAG 物料统一保存为正式 KnowledgeDocument；前端知识库页、
        # Agent RAG 和重启恢复只读取这一份事实数据。
        admin = await db.scalar(select(User)
                                .where(User.role.in_(["admin", "superadmin"]),
                                       User.is_deleted == 0)
                                .order_by(User.id)
                                .limit(1))
        kb_created = False
        kb_docs_added = 0
        kb_docs_updated = 0
        raw_materials_skipped = 0
        if admin:
            kb = await db.scalar(select(KnowledgeBase).where(
                KnowledgeBase.uid == admin.uid, KnowledgeBase.name == "业务知识库"
            ))
            if not kb:
                kb = KnowledgeBase(
                    id=uuid.uuid4().hex,
                    uid=admin.uid,
                    name="业务知识库",
                    description="由 Wiki 与数仓业务文档整理的 DataAgent 业务知识。",
                    collection_name=f"datadeck_kb_{uuid.uuid4().hex}",
                )
                db.add(kb)
                await db.flush()
                kb_created = True
            for material in materials:
                if material["source_type"] not in BUSINESS_SOURCE_TYPES:
                    # 代码、OMD 快照和关键词抽取结果不是普通业务文档事实源；
                    # 它们由 Git/OMD 专用入口或公共物料空间提供，避免重复写入 RAG。
                    raw_materials_skipped += 1
                    continue
                existing = await db.scalar(select(KnowledgeDocument).where(
                    KnowledgeDocument.kb_id == kb.id,
                    KnowledgeDocument.source_type == material["source_type"],
                    KnowledgeDocument.source_id == material["source_id"],
                ))
                if existing and existing.content_hash == material["content_hash"]:
                    continue
                if existing:
                    await delete_document(db, admin.uid, kb.id, existing.id)
                    kb_docs_updated += 1
                await add_document(
                    db, admin.uid, kb.id,
                    _knowledge_filename(material),
                    material["content"].encode("utf-8"),
                    source_type=material["source_type"],
                    source_id=material["source_id"],
                    layer=material["layer"],
                    metadata=material["metadata"],
                )
                kb_docs_added += 1
        await db.commit()
    print(json.dumps({"materials": len(materials), "semantic_metrics": len(metrics),
                      "candidate_metrics_added": len(candidate_metrics),
                      "business_kb_created": kb_created, "business_kb_docs_added": kb_docs_added,
                      "business_kb_docs_updated": kb_docs_updated,
                      "raw_materials_skipped": raw_materials_skipped}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
