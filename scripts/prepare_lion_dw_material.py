#!/usr/bin/env python3
"""Create reviewable inventory and metric candidates from ../lion_dw."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("../lion_dw/app").resolve()
OUT = Path("data_material/lion_dw")
TABLE_RE = re.compile(r"\b(?:from|join|insert\s+(?:overwrite\s+)?table|create\s+table(?:\s+if\s+not\s+exists)?)\s+([a-zA-Z_][\w]*\.[a-zA-Z_][\w]*)", re.I)
TARGET_RE = re.compile(r"(?:insert\s+(?:overwrite\s+)?table|create\s+table(?:\s+if\s+not\s+exists)?)\s+([a-zA-Z_][\w]*\.[a-zA-Z_][\w]*)", re.I)
METRIC_RE = re.compile(r"\b(sum|count|avg|max|min)\s*\([^\n]{1,180}\)\s*(?:as\s+)?([a-zA-Z_][\w]*)?\s*(?:--|#)\s*(.+)$", re.I)
TABLE_COMMENT_RE = re.compile(r"\)\s*comment\s*['\"]([^'\"]+)", re.I)
FIELD_COMMENT_RE = re.compile(r"^\s*`?([a-zA-Z_][\w]*)`?\s+([a-zA-Z]+(?:\s*\([^)]*\))?)\s+comment\s*['\"]([^'\"]*)", re.I)


def line_records(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


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
