#!/usr/bin/env python3
"""Create reviewable inventory and metric candidates from ../lion_dw."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    import sqlglot
    from sqlglot import exp
except ImportError:  # 允许仅做文件清单扫描的最小环境运行
    sqlglot = None
    exp = None


ROOT = Path("../lion_dw/app").resolve()
OUT = Path("data_material/lion_dw")
TABLE_RE = re.compile(r"\b(?:from|join|insert\s+(?:overwrite\s+)?table|create\s+table(?:\s+if\s+not\s+exists)?)\s+([a-zA-Z_][\w]*\.[a-zA-Z_][\w]*)", re.I)
TARGET_RE = re.compile(r"(?:insert\s+(?:overwrite\s+)?table|create\s+table(?:\s+if\s+not\s+exists)?)\s+([a-zA-Z_][\w]*\.[a-zA-Z_][\w]*)", re.I)
METRIC_RE = re.compile(r"\b(sum|count|avg|max|min)\s*\([^\n]{1,180}\)\s*(?:as\s+)?([a-zA-Z_][\w]*)?\s*(?:--|#)\s*(.+)$", re.I)
TABLE_COMMENT_RE = re.compile(r"\)\s*comment\s*['\"]([^'\"]+)", re.I)
FIELD_COMMENT_RE = re.compile(r"^\s*`?([a-zA-Z_][\w]*)`?\s+([a-zA-Z]+(?:\s*\([^)]*\))?)\s+comment\s*['\"]([^'\"]*)", re.I)


def line_records(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def ast_metadata(text: str) -> dict:
    """提取 SQL AST 证据；解析失败只标记状态，不丢失正则扫描结果。"""
    if sqlglot is None or exp is None:
        return {"parse_status": "dependency_missing", "ast_tables": [], "ast_columns": [], "ast_features": {}}
    try:
        statements = sqlglot.parse(text, read="hive")
        tables = sorted({item.sql(dialect="hive") for statement in statements for item in statement.find_all(exp.Table)})
        columns = sorted({item.sql(dialect="hive") for statement in statements for item in statement.find_all(exp.Column)})
        features = {
            "joins": sorted({item.sql(dialect="hive") for statement in statements for item in statement.find_all(exp.Join)}),
            "filters": sorted({item.this.sql(dialect="hive") for statement in statements for item in statement.find_all(exp.Where)}),
            "aggregations": sorted({item.sql(dialect="hive") for statement in statements for item in statement.find_all(exp.AggFunc)}),
            "partitions": sorted(set(re.findall(r"\b(?:partition|dt)\s*(?:=|\(|by)?[^\n,)]*", text, re.I))),
        }
        return {"parse_status": "ok", "ast_tables": tables, "ast_columns": columns, "ast_features": features}
    except Exception as exc:  # noqa: BLE001 — 单文件失败不能阻断全量盘点
        return {"parse_status": "failed", "parse_error": type(exc).__name__, "ast_tables": [], "ast_columns": [], "ast_features": {}}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    inventory = []
    candidates = []
    for path in sorted(ROOT.rglob("*")):
        if path.suffix.lower() not in {".py", ".sql"}:
            continue
        lines = line_records(path)
        text = "\n".join(lines)
        tables = sorted(set(m.group(1).lower() for m in TABLE_RE.finditer(text)))
        targets = sorted(set(m.group(1).lower() for m in TARGET_RE.finditer(text)))
        ast = ast_metadata(text) if path.suffix.lower() in {".sql", ".hql"} else {
            "parse_status": "not_sql", "ast_tables": [], "ast_columns": [], "ast_features": {}
        }
        fields = []
        for number, line in enumerate(lines, 1):
            match = FIELD_COMMENT_RE.search(line)
            if match:
                fields.append({"name": match.group(1), "type": match.group(2), "description": match.group(3), "line": number})
            metric = METRIC_RE.search(line)
            if metric:
                candidates.append({
                    "candidate_id": f"code:{path}:{number}",
                    "name": metric.group(2) or metric.group(1).lower(),
                    "aggregation": metric.group(1).upper(),
                    "comment": metric.group(3).strip(),
                    "source_path": str(path),
                    "source_line": number,
                    "status": "candidate",
                })
        inventory.append({
            "path": str(path),
            "relative_path": str(path.relative_to(ROOT.parent)),
            "language": path.suffix.lower().lstrip("."),
            "line_count": len(lines),
            "tables": tables,
            "targets": targets,
            **ast,
            "partitioned": bool(re.search(r"\bpartition(?:ed)?\b|\bdt\s*=", text, re.I)),
            "run_date_parameter": "{run_date}" in text,
            "table_comment": (TABLE_COMMENT_RE.search(text).group(1) if TABLE_COMMENT_RE.search(text) else ""),
            "fields": fields,
            "generated_at": generated_at,
        })

    for name, records in [("code_inventory.jsonl", inventory), ("metric_candidates.jsonl", candidates)]:
        (OUT / name).write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
    summary = {
        "generated_at": generated_at,
        "root": str(ROOT),
        "files": len(inventory),
        "metric_candidates": len(candidates),
        "tables_seen": len({table for item in inventory for table in item["tables"]}),
        "files_with_partition": sum(1 for item in inventory if item["partitioned"]),
    }
    (OUT / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
