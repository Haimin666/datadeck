#!/usr/bin/env python3
"""Build reviewable business docs, Ossie draft metrics and OMD scope material."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


WIKI_DIR = Path("data_material/wiki/70331927")
CODE_DIR = Path("../lion_dw/app").resolve()
LION_DIR = Path("data_material/lion_dw")
OMD_DIR = Path("data_material/omd")
OUT = Path("data_material/delivery")

CATEGORIES = {
    "征信规则与口径": re.compile(r"规则|上报|报文|ETC|反担保|删除|呆账|迁移", re.I),
    "征信数据与数据库": re.compile(r"数据|数据库|hive|重构|基础信息", re.I),
    "征信监控与产出": re.compile(r"监控|短信|邮箱|备份|评分", re.I),
    "征信问题与处理": re.compile(r"问题|反馈|调整|处理记录|确认", re.I),
    "征信业务流程": re.compile(r"流程|个人|企业|卓远", re.I),
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def wiki_pages() -> list[dict]:
    manifest = json.loads((WIKI_DIR / "manifest.json").read_text(encoding="utf-8"))
    result = []
    for item in manifest["pages"]:
        path = Path(item.get("path", ""))
        if not path.is_absolute():
            path = Path(path)
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        body = text.split("---\n", 2)[-1].strip() if "---\n" in text else text
        result.append({**item, "body": body})
    return result


def category_for(page: dict) -> str:
    haystack = f"{page.get('title', '')}\n{page.get('body', '')[:2000]}"
    for category, pattern in CATEGORIES.items():
        if pattern.search(haystack):
            return category
    return "征信其他资料"


def build_business_docs(pages: list[dict], generated_at: str) -> None:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for page in pages:
        grouped[category_for(page)].append(page)
    for category, category_pages in sorted(grouped.items()):
        code_hits = []
        pattern = CATEGORIES.get(category, re.compile(r"征信|信用|上报", re.I))
        for path in sorted(CODE_DIR.rglob("*")):
            if path.suffix.lower() not in {".py", ".sql"}:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for number, line in enumerate(lines, 1):
                if ("--" in line or "#" in line) and pattern.search(line):
                    code_hits.append({"path": str(path), "line": number, "text": line.strip()})
        code_hits = code_hits[:80]
        body = [f"# {category}", "", "> 生成状态：draft，Wiki 与数仓代码均为证据，指标口径需人工确认。", "", f"生成时间：{generated_at}", "", "## Wiki 业务文档", ""]
        for page in category_pages:
            body.extend([f"### {page['title']}", f"- 页面 ID：`{page['id']}`", f"- 来源：{page.get('url', '')}", f"- 层级：{page.get('level', 0)}", "", page.get("body", "")[:12000], ""])
        body.extend(["## 数仓代码注释与逻辑证据", "", "以下只收录命中本主题的注释行，不能单独视为业务定义：", ""])
        for hit in code_hits:
            body.append(f"- `{hit['path']}:{hit['line']}` — {hit['text']}")
        body.extend(["", "## 待人工确认", "", "- 业务定义、统计粒度、时间窗口、单位和过滤条件", "- Wiki 口径与当前代码实现不一致的地方", "- 代码中只表达实现逻辑但没有业务定义的指标", ""])
        filename = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", category).strip("_") + ".md"
        (OUT / "business_docs" / filename).parent.mkdir(parents=True, exist_ok=True)
        (OUT / "business_docs" / filename).write_text("\n".join(body), encoding="utf-8")


def build_metrics(generated_at: str) -> dict:
    raw = read_jsonl(LION_DIR / "metric_candidates.jsonl")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in raw:
        label = re.sub(r"\s+", " ", (item.get("comment") or item.get("name") or "unknown").strip()).lower()
        key = re.sub(r"[^\w\u4e00-\u9fff]+", "", label) or item.get("name", "unknown").lower()
        grouped[key].append(item)
    metrics = []
    review = []
    for key, items in sorted(grouped.items()):
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        label = items[0].get("comment") or items[0].get("name") or key
        expressions = []
        evidence = []
        for item in items:
            path = Path(item["source_path"])
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
            source_line = lines[item["source_line"] - 1].strip() if 0 < item["source_line"] <= len(lines) else ""
            expr = re.split(r"\s*(?:--|#)\s*", source_line, maxsplit=1)[0].strip().rstrip(",")
            expressions.append(re.sub(r"\s+", " ", expr).lower())
            evidence.append({"path": item["source_path"], "line": item["source_line"], "comment": item.get("comment", ""), "expression": expr})
        distinct = sorted(set(expressions))
        conflict = len(distinct) > 1
        expression = evidence[0]["expression"] or "SUM(0)"
        expression_status = "extracted"
        try:
            import sqlglot
            sqlglot.parse_one(expression, read="hive")
        except Exception:
            expression = "COUNT(1)" if any(x.get("aggregation") == "COUNT" for x in items) else "SUM(0)"
            expression_status = "needs_review"
        metric = {
            "name": f"metric_{digest}",
            "description": f"{label}（代码候选，待业务确认）",
            "datatype": "Integer" if any(x.get("aggregation") == "COUNT" for x in items) else "Decimal",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": expression}]},
            "ai_context": {"synonyms": sorted(set([label] + [x.get("name", "") for x in items if x.get("name")])), "review_status": "needs_review"},
            "custom_extensions": [{"vendor_name": "DATADECK", "data": json.dumps({"candidate_key": key, "conflict": conflict, "expression_status": expression_status, "original_expressions": sorted(set(x["expression"] for x in evidence)), "evidence": evidence}, ensure_ascii=False)}],
        }
        metrics.append(metric)
        review.append({"metric_name": metric["name"], "display_name": label, "candidate_count": len(items), "conflict": conflict, "evidence": evidence, "status": "needs_review"})
    (OUT / "metrics").mkdir(parents=True, exist_ok=True)
    (OUT / "metrics/metric_review.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in review) + "\n", encoding="utf-8")
    semantic = {"version": "0.2.0.dev0", "semantic_model": [{"name": "lion_dw_draft_semantic_model", "description": "从 Wiki 与数仓代码抽取的待审核指标语义模型", "ai_context": {"instructions": "只使用 review_status=approved 的指标；draft/needs_review 必须提示人工确认"}, "datasets": [{"name": "lion_dw_pending_review", "source": "数仓HIVE.default.lion_dw_*", "description": "由 Hive lion_dw_* schema 承载的待审核语义数据集", "fields": []}], "relationships": [], "metrics": metrics, "custom_extensions": [{"vendor_name": "DATADECK", "data": json.dumps({"generated_at": generated_at, "status": "draft", "metric_count": len(metrics)}, ensure_ascii=False)}]}]}
    try:
        import yaml
        (OUT / "metrics/semantic_model.yaml").write_text(yaml.safe_dump(semantic, allow_unicode=True, sort_keys=False), encoding="utf-8")
    except ImportError:
        (OUT / "metrics/semantic_model.json").write_text(json.dumps(semantic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"raw_candidates": len(raw), "unique_metrics": len(metrics), "conflicts": sum(1 for x in review if x["conflict"])}


def build_omd_scope(generated_at: str) -> dict:
    records = read_jsonl(OMD_DIR / "table_metadata.jsonl") if (OMD_DIR / "table_metadata.jsonl").exists() else []
    matches = [x for x in records if x.get("schema", "").lower().startswith("lion_dw_")]
    service = records[0].get("service", "数仓HIVE") if records else "数仓HIVE"
    database = records[0].get("database", "default") if records else "default"
    scope = {"generated_at": generated_at, "service": service, "database": database, "filter": "schema name starts with lion_dw_", "omd_records_examined": len(records), "matched_records": len(matches), "status": "ready_for_incremental_sync" if matches else "no_match_in_current_omd_snapshot", "tables": matches, "note": "仅纳入 Hive 中 schema 名以 lion_dw_ 开头的库表；代码侧 lion_dw_* 资产见 data_material/lion_dw/code_inventory.jsonl。"}
    (OUT / "omd").mkdir(parents=True, exist_ok=True)
    (OUT / "omd/lion_dw_scope.json").write_text(json.dumps(scope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"omd_examined": len(records), "omd_matches": len(matches)}


def main() -> None:
    generated_at = datetime.now(timezone.utc).isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    pages = wiki_pages()
    build_business_docs(pages, generated_at)
    metrics = build_metrics(generated_at)
    omd = build_omd_scope(generated_at)
    hot = {"policy": "hot_cold", "hot": "近90天更新、已审核指标、当前活跃表描述和最近产出快照", "cold": "历史 Wiki 原文、旧版本指标、原始代码证据、已失效或低频表", "retrieval": "热层优先；冷层仅在用户指定历史范围或热层低置信度时检索", "source_of_truth": {"business_definition": "wiki_and_approved_metric", "table_lineage": "omd", "implementation_evidence": "lion_dw_code", "production_status": "runtime_partition_and_job_snapshot"}, "generated_at": generated_at}
    (OUT / "hot_cold_policy.json").write_text(json.dumps(hot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {"generated_at": generated_at, "wiki_pages": len(pages), "business_docs": len(list((OUT / "business_docs").glob("*.md"))), **metrics, **omd}
    (OUT / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
