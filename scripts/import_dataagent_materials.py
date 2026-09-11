"""将 data_material 当前交付物幂等导入 DataDeck PostgreSQL。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from sqlalchemy import select

from server.db import async_session_factory, engine
from server.models import AgentMaterial, Base
from server.services.metric_registry import MetricRegistry
from server.utils.datetime_utils import utc_now_naive

MATERIAL_ROOT = ROOT / "data_material"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _material(source_type: str, source_id: str, title: str, content, *, layer="cold", metadata=None):
    text = content if isinstance(content, str) else _json(content)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = hashlib.sha256(f"{source_type}:{source_id}".encode()).hexdigest()[:64]
    return AgentMaterial(id=key, source_type=source_type, source_id=source_id,
                         title=title[:512], content=text, metadata_json=metadata or {},
                         layer=layer, content_hash=digest, updated_at=utc_now_naive())


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
        evidence = review.get(metric["name"], {}).get("evidence", [])
        conflict = bool(review.get(metric["name"], {}).get("conflict"))
        ai_context = metric.get("ai_context") or {}
        yield MetricRegistry(canonical_name=metric.get("description", metric["name"]).replace("（代码候选，待业务确认）", "").strip(),
                             aliases=ai_context.get("synonyms", []),
                             definition=metric.get("description", ""),
                             formula=(evidence[0].get("expression") if evidence else None),
                             ossie_name=metric["name"], ossie_expression=metric.get("expression"),
                             datatype=metric.get("datatype"), ai_context=ai_context,
                             source_evidence=evidence, status="needs_review" if conflict else "approved",
                             conflict_status="conflict" if conflict else "none",
                             conflict_details=evidence if conflict else [])


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    materials = collect_materials()
    metrics = list(collect_metrics())
    async with async_session_factory() as db:
        for item in materials:
            existing = await db.get(AgentMaterial, item.id)
            if existing:
                for key in ("source_type", "source_id", "title", "content", "metadata_json", "layer", "content_hash", "updated_at"):
                    setattr(existing, key, getattr(item, key))
            else:
                db.add(item)
        for item in metrics:
            existing = await db.scalar(select(MetricRegistry).where(MetricRegistry.ossie_name == item.ossie_name))
            if existing:
                for key in ("canonical_name", "aliases", "definition", "formula", "ossie_expression", "datatype", "ai_context", "source_evidence", "status", "conflict_status", "conflict_details", "updated_at"):
                    setattr(existing, key, getattr(item, key))
            else:
                db.add(item)
        await db.commit()
    print(json.dumps({"materials": len(materials), "metrics": len(metrics)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
