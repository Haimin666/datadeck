"""OpenMetadata 元数据查询层（移植自 ~/.agents/skills/omd-query/scripts/omd_query.py）。

env 配置（未配置时工具返回占位提示，不影响链路）：
- OMD_BASE_URL: 如 https://omd.corp.shiqiao.com/api/v1
- OMD_TOKEN:    Bearer xxx
- OMD_SERVICE:  默认 数仓Doris
- OMD_DATABASE: 默认 default
"""

from __future__ import annotations

import os
import ssl
import urllib.parse
import urllib.request
from typing import Any

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def omd_base_url() -> str:
    return os.getenv("OMD_BASE_URL", "").rstrip("/")


def omd_token() -> str:
    t = os.getenv("OMD_TOKEN", "")
    if t and not t.startswith("Bearer "):
        t = f"Bearer {t}"
    return t


def omd_service() -> str:
    return os.getenv("OMD_SERVICE", "数仓Doris")


def omd_database() -> str:
    return os.getenv("OMD_DATABASE", "default")


def omd_configured() -> bool:
    return bool(omd_base_url() and omd_token())


def _placeholder(action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"OpenMetadata 未配置（OMD_BASE_URL/OMD_TOKEN），无法{action}。"
                 "当前为占位状态，请联系管理员配置企业元数据服务。",
    }


def _api_get(path: str, retries: int = 2) -> dict[str, Any] | None:
    """GET 请求，返回 None 表示失败（token 过期/网络问题）。"""
    req = urllib.request.Request(
        omd_base_url() + path,
        headers={"Authorization": omd_token()},
    )
    import time
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, context=_ctx, timeout=30) as resp:
                import json
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            if attempt < retries - 1:
                time.sleep(1)
    return None


def _paginate(path_base: str, limit: int = 500) -> list[dict]:
    """cursor 翻页取全量。"""
    items: list[dict] = []
    after: str | None = None
    while True:
        path = f"{path_base}&limit={limit}"
        if after:
            path += f"&after={urllib.parse.quote(after)}"
        data = _api_get(path)
        if not data:
            break
        batch = data.get("data", [])
        items.extend(batch)
        after = data.get("paging", {}).get("after")
        if not after or not batch:
            break
    return items


def _pg_fallback(action: str) -> dict[str, Any] | None:
    """OMD 未配置/失败时，从 DATADECK_SQL_DSN 的 information_schema 兜底取真实元数据。

    返回 None 表示 DSN 也不可用（彻底占位由调用方处理）。
    """
    dsn = os.getenv("DATADECK_SQL_DSN", "")
    if not dsn or not dsn.startswith(("postgresql://", "postgres://")):
        return None
    import asyncio

    try:
        import asyncpg

        async def run(coro):
            return await coro

        async def query(sql: str):
            conn = await asyncpg.connect(dsn.replace("postgresql://", "postgres://", 1))
            try:
                return await conn.fetch(sql)
            finally:
                await conn.close()

        if action == "databases":
            rows = asyncio.run(query(
                "SELECT schema_name AS name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('pg_catalog','information_schema') ORDER BY 1"))
            return {"ok": True, "source": "pg_fallback",
                    "schemas": [{"name": r["name"], "fqn": r["name"]} for r in rows]}
        return None
    except Exception as exc:  # noqa: BLE001
        return None


def list_databases() -> dict[str, Any]:
    if not omd_configured():
        fb = _pg_fallback("databases")
        if fb:
            return fb
        return _placeholder("列出数据库")
    data = _api_get(f"/databases?service={urllib.parse.quote(omd_service())}&limit=500")
    if not data:
        return {"ok": False, "error": "OpenMetadata 查询失败（token 可能过期，需更新 OMD_TOKEN）"}
    return {
        "ok": True,
        "service": omd_service(),
        "databases": [
            {"name": d.get("name", ""), "fqn": d.get("fullyQualifiedName", "")}
            for d in data.get("data", [])
        ],
    }


def list_schemas(database: str | None = None) -> dict[str, Any]:
    if not omd_configured():
        fb = _pg_fallback("databases")
        if fb:
            return fb
        return _placeholder("列出 Schema")
    db_fqn = urllib.parse.quote(f"{omd_service()}.{database or omd_database()}")
    data = _api_get(f"/databaseSchemas?database={db_fqn}&limit=500")
    if not data:
        return {"ok": False, "error": "OpenMetadata 查询失败（token 可能过期）"}
    return {
        "ok": True,
        "database": database or omd_database(),
        "schemas": [
            {"name": s.get("name", ""), "fqn": s.get("fullyQualifiedName", "")}
            for s in data.get("data", [])
        ],
    }


def list_tables(schema: str, database: str | None = None) -> dict[str, Any]:
    if not omd_configured():
        fb = _pg_fallback_tables(schema)
        if fb is not None:
            return fb
        return _placeholder("列出表")
    schema_fqn = urllib.parse.quote(f"{omd_service()}.{database or omd_database()}.{schema}")
    tables = _paginate(f"/tables?databaseSchema={schema_fqn}")
    if not tables and not _api_get(f"/tables?databaseSchema={schema_fqn}&limit=1"):
        return {"ok": False, "error": "OpenMetadata 查询失败（token 可能过期）"}
    return {
        "ok": True,
        "schema": schema,
        "total": len(tables),
        "tables": [
            {
                "name": t.get("name", ""),
                "fqn": t.get("fullyQualifiedName", ""),
                "description": (t.get("description") or "")[:200],
            }
            for t in tables
        ],
    }


def _pg_fallback_tables(schema: str) -> dict[str, Any] | None:
    """PG information_schema 兜底：列某 schema 的表。DSN 不可用返回 None。"""
    dsn = os.getenv("DATADECK_SQL_DSN", "")
    if not dsn or not dsn.startswith(("postgresql://", "postgres://")):
        return None
    try:
        import asyncio

        import asyncpg

        async def query():
            conn = await asyncpg.connect(dsn.replace("postgresql://", "postgres://", 1))
            try:
                return await conn.fetch(
                    "SELECT table_name, obj_description(format('%s.%s', table_schema, table_name)::regclass) AS descr "
                    "FROM information_schema.tables WHERE table_schema=$1 AND table_type='BASE TABLE' ORDER BY 1",
                    schema,
                )
            finally:
                await conn.close()

        rows = asyncio.run(query())
        return {
            "ok": True, "source": "pg_fallback", "schema": schema, "total": len(rows),
            "tables": [
                {"name": r["table_name"], "fqn": f"{schema}.{r['table_name']}",
                 "description": (r["descr"] or "")[:200]}
                for r in rows
            ],
        }
    except Exception:  # noqa: BLE001
        return None


def _pg_fallback_table_schema(schema: str, table: str) -> dict[str, Any] | None:
    """PG information_schema 兜底：查某表字段（含类型+注释）。DSN 不可用返回 None。"""
    dsn = os.getenv("DATADECK_SQL_DSN", "")
    if not dsn or not dsn.startswith(("postgresql://", "postgres://")):
        return None
    try:
        import asyncio

        import asyncpg

        async def query():
            conn = await asyncpg.connect(dsn.replace("postgresql://", "postgres://", 1))
            try:
                return await conn.fetch(
                    "SELECT c.column_name, c.data_type, "
                    "col_description(format('%s.%s', c.table_schema, c.table_name)::regclass, c.ordinal_position) AS col_desc "
                    "FROM information_schema.columns c "
                    "WHERE c.table_schema=$1 AND c.table_name=$2 ORDER BY c.ordinal_position",
                    schema, table,
                )
            finally:
                await conn.close()

        rows = asyncio.run(query())
        if not rows:
            return {"ok": False, "business": True,
                    "error": f"表 {schema}.{table} 在数据源中不存在（information_schema 无此表）。"
                             "请确认 schema/table 名，或用 omd_list_tables 浏览真实表清单。"}
        return {
            "ok": True, "source": "pg_fallback", "table": table, "schema": schema,
            "description": "", "column_count": len(rows),
            "columns": [
                {"name": r["column_name"], "type": r["data_type"],
                 "description": (r["col_desc"] or "")[:120], "tags": []}
                for r in rows
            ],
        }
    except Exception:  # noqa: BLE001
        return None


def get_table_schema(schema: str, table: str, database: str | None = None) -> dict[str, Any]:
    """表结构：字段名/类型/注释（Text2SQL 的核心弹药）。"""
    if not omd_configured():
        fb = _pg_fallback_table_schema(schema, table)
        if fb is not None:
            return fb
        return _placeholder("查询表结构")
    table_fqn = urllib.parse.quote(
        f"{omd_service()}.{database or omd_database()}.{schema}.{table}")
    data = _api_get(f"/tables/name/{table_fqn}")
    if not data:
        return {"ok": False, "error": f"表 {schema}.{table} 查询失败（不存在或 token 过期）"}
    columns = [
        {
            "name": c.get("name", ""),
            "type": c.get("dataType", "") or c.get("dataTypeDisplay", ""),
            "description": (c.get("description") or "")[:120],
            "tags": [t.get("tagFQN", "") for t in (c.get("tags") or []) if t.get("tagFQN")],
        }
        for c in (data.get("columns") or [])
    ]
    return {
        "ok": True,
        "table": table,
        "schema": schema,
        "description": (data.get("description") or "")[:300],
        "column_count": len(columns),
        "columns": columns,
    }


def get_table_lineage(
    schema: str, table: str, depth: int = 1, direction: str = "both",
    database: str | None = None,
) -> dict[str, Any]:
    if not omd_configured():
        return _placeholder("查询血缘")
    up_depth = depth if direction in ("both", "up") else 0
    down_depth = depth if direction in ("both", "down") else 0
    fqn = urllib.parse.quote(f"{omd_service()}.{database or omd_database()}.{schema}.{table}")
    data = _api_get(
        f"/lineage/getLineage?fqn={fqn}&upstreamDepth={up_depth}"
        f"&downstreamDepth={down_depth}&type=table"
    )
    if not data:
        return {"ok": False, "error": "血缘查询失败（表不存在或 token 过期）"}
    upstream = [
        e.get("fromEntity", {}).get("fullyQualifiedName", "")
        for e in (data.get("upstreamEdges") or [])
    ]
    downstream = [
        e.get("toEntity", {}).get("fullyQualifiedName", "")
        for e in (data.get("downstreamEdges") or [])
    ]
    return {
        "ok": True,
        "table": f"{schema}.{table}",
        "upstream": upstream,
        "downstream": downstream,
    }


def test_connection() -> dict[str, Any]:
    if not omd_configured():
        return {"ok": False, "error": "OMD_BASE_URL/OMD_TOKEN 未配置"}
    data = _api_get(f"/databases?service={urllib.parse.quote(omd_service())}&limit=1")
    if data is None:
        return {"ok": False, "error": "连接失败（网络或 token 过期）"}
    return {"ok": True, "service": omd_service()}
