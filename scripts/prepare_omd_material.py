#!/usr/bin/env python3
"""Snapshot OpenMetadata schemas, tables and table descriptions without proxy."""

from __future__ import annotations

import json
import os
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


OUT = Path("data_material/omd")
BASE = os.getenv("OMD_BASE_URL", "").rstrip("/")
TOKEN = os.getenv("OMD_TOKEN", "")
SERVICE = os.getenv("OMD_SERVICE", "数仓Doris")
DATABASE = os.getenv("OMD_DATABASE", "default")
SCHEMA_FILTER = {item.strip() for item in os.getenv("OMD_SCHEMAS", "").split(",") if item.strip()}
DETAIL_LIMIT = int(os.getenv("OMD_DETAIL_LIMIT", "200"))
DETAIL_SCHEMAS = {item.strip() for item in os.getenv("OMD_DETAIL_SCHEMAS", "").split(",") if item.strip()}


def opener() -> urllib.request.OpenerDirector:
    verify = os.getenv("DATADECK_OMD_SSL_VERIFY", "true").strip().lower()
    context = ssl.create_default_context()
    if verify in {"0", "false", "no", "off"}:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
    )


def get(client: urllib.request.OpenerDirector, path: str) -> dict:
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": TOKEN if TOKEN.startswith("Bearer ") else f"Bearer {TOKEN}"},
    )
    with client.open(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def all_tables(client: urllib.request.OpenerDirector, schema: str) -> list[dict]:
    result = []
    after = None
    while True:
        params = {"databaseSchema": f"{SERVICE}.{DATABASE}.{schema}", "limit": "500"}
        if after:
            params["after"] = after
        data = get(client, "/tables?" + urllib.parse.urlencode(params))
        batch = data.get("data", [])
        result.extend(batch)
        after = (data.get("paging") or {}).get("after")
        if not after or not batch:
            return result


def main() -> None:
    if not BASE or not TOKEN:
        raise SystemExit("OMD_BASE_URL and OMD_TOKEN are required")
    client = opener()
    OUT.mkdir(parents=True, exist_ok=True)
    collected_at = datetime.now(timezone.utc).isoformat()
    schema_data = get(client, "/databaseSchemas?" + urllib.parse.urlencode({
        "database": f"{SERVICE}.{DATABASE}", "limit": "500"}))
    schemas = [s.get("name", "") for s in schema_data.get("data", [])
               if s.get("name") and (not SCHEMA_FILTER or s.get("name") in SCHEMA_FILTER)]
    tables_out = []
    failures = []
    detail_count = 0
    for schema in schemas:
        for table in all_tables(client, schema):
            fqn = table.get("fullyQualifiedName") or f"{SERVICE}.{DATABASE}.{schema}.{table.get('name', '')}"
            record = {
                "source_type": "omd",
                "service": SERVICE,
                "database": DATABASE,
                "schema": schema,
                "name": table.get("name", ""),
                "fqn": fqn,
                "description": table.get("description") or "",
                "owners": table.get("owners") or [],
                "tags": table.get("tags") or [],
                "updated_at": table.get("updatedAt"),
                "collected_at": collected_at,
            }
            try:
                if DETAIL_SCHEMAS and schema not in DETAIL_SCHEMAS:
                    record["columns"] = []
                    record["status"] = "cold_list_only"
                    tables_out.append(record)
                    continue
                if detail_count >= DETAIL_LIMIT:
                    record["columns"] = []
                    record["status"] = "list_only"
                    tables_out.append(record)
                    continue
                detail = get(client, "/tables/name/" + urllib.parse.quote(fqn, safe=""))
                detail_count += 1
                record["description"] = detail.get("description") or record["description"]
                record["columns"] = detail.get("columns") or []
                record["owners"] = detail.get("owners") or record["owners"]
                record["tags"] = detail.get("tags") or record["tags"]
                record["updated_at"] = detail.get("updatedAt") or record["updated_at"]
                record["status"] = "ok"
            except Exception as exc:  # noqa: BLE001
                record["columns"] = []
                record["status"] = "detail_failed"
                failures.append({"fqn": fqn, "error_type": type(exc).__name__})
            tables_out.append(record)
        (OUT / "table_metadata.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in tables_out) + "\n", encoding="utf-8")
    (OUT / "table_metadata.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in tables_out) + "\n", encoding="utf-8")
    summary = {
        "source_type": "omd",
        "service": SERVICE,
        "database": DATABASE,
        "collected_at": collected_at,
        "schema_count": len(schemas),
        "table_count": len(tables_out),
        "detail_failed": len(failures),
        "schemas": schemas,
        "failures": failures,
    }
    (OUT / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("schema_count", "table_count", "detail_failed")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
