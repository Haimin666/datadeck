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
import asyncio
import urllib.parse
import urllib.request
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Coroutine
from typing import Any

def _ssl_context() -> ssl.SSLContext:
    """默认校验证书；仅在明确配置时允许内网自签名证书。"""
    verify = os.getenv("DATADECK_OMD_SSL_VERIFY", "true").strip().lower()
    if verify in {"0", "false", "no", "off"}:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    return ssl.create_default_context()


_DIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=_ssl_context()),
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
)


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


def list_services() -> dict[str, Any]:
    """列出当前 OMD Token 可见的全部数据库类 Service。"""
    if not omd_configured():
        return _placeholder("列出 Service")
    data = _api_get("/services/databaseServices?limit=500")
    if not data:
        return {"ok": False, "error": "OpenMetadata Service 查询失败（token 可能过期）"}
    services = [
        {
            "name": item.get("name", ""),
            "fqn": item.get("fullyQualifiedName", ""),
            "service_type": item.get("serviceType", ""),
        }
        for item in data.get("data", [])
        if item.get("name")
    ]
    return {"ok": True, "services": services}


def _run_async_query(factory: Callable[[], Coroutine[Any, Any, list]]) -> list | None:
    """执行同步工具的异步兜底查询，兼容已有事件循环。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    # 某些兼容调用方会直接在 async 上下文调用同步 OMD 函数。
    # 不能在当前线程嵌套 asyncio.run；单独线程运行短生命周期 loop，
    # 保持同步 API 的返回契约，同时避免直接抛 RuntimeError。
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="omd-fallback") as pool:
        return pool.submit(asyncio.run, factory()).result()


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
            with _DIRECT_OPENER.open(req, timeout=30) as resp:
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
    seen_after: set[str] = set()
    while True:
        path = f"{path_base}&limit={limit}"
        if after:
            if after in seen_after:
                # OMD 异常返回相同游标时停止，避免工具线程永久循环。
                break
            seen_after.add(after)
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
    try:
        import asyncpg

        async def query(sql: str):
            conn = await asyncpg.connect(dsn.replace("postgresql://", "postgres://", 1))
            try:
                return await conn.fetch(sql)
            finally:
                await conn.close()

        if action == "databases":
            rows = _run_async_query(lambda: query(
                "SELECT schema_name AS name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('pg_catalog','information_schema') ORDER BY 1"))
            if rows is None:
                return None
            return {"ok": True, "source": "pg_fallback",
                    "schemas": [{"name": r["name"], "fqn": r["name"]} for r in rows]}
        return None
    except Exception:  # noqa: BLE001
        return None


def list_databases(service: str | None = None) -> dict[str, Any]:
    if not omd_configured():
        fb = _pg_fallback("databases")
        if fb:
            return fb
        return _placeholder("列出数据库")
    selected = str(service or "").strip()
    services = [selected] if selected else [
        item["name"] for item in list_services().get("services", [])
    ]
    if not services:
        services = [omd_service()]
    databases = []
    failed = []
    for service_name in services:
        data = _api_get(
            f"/databases?service={urllib.parse.quote(service_name)}&limit=500"
        )
        if not data:
            failed.append(service_name)
            continue
        databases.extend({
            "name": item.get("name", ""),
            "fqn": item.get("fullyQualifiedName", ""),
            "service": service_name,
        } for item in data.get("data", []) if item.get("name"))
    return {
        "ok": bool(databases) or not failed,
        "services": services,
        "databases": databases,
        "failed_services": failed,
    }


def list_schemas(database: str | None = None, service: str | None = None) -> dict[str, Any]:
    if not omd_configured():
        fb = _pg_fallback("databases")
        if fb:
            return fb
        return _placeholder("列出 Schema")
    service_name = str(service or omd_service()).strip()
    db_fqn = urllib.parse.quote(f"{service_name}.{database or omd_database()}")
    data = _api_get(f"/databaseSchemas?database={db_fqn}&limit=500")
    if not data:
        return {"ok": False, "error": "OpenMetadata 查询失败（token 可能过期）"}
    return {
        "ok": True,
        "service": service_name,
        "database": database or omd_database(),
        "schemas": [
            {"name": s.get("name", ""), "fqn": s.get("fullyQualifiedName", "")}
            for s in data.get("data", [])
        ],
    }


def list_tables(schema: str, database: str | None = None, service: str | None = None) -> dict[str, Any]:
    if not omd_configured():
        fb = _pg_fallback_tables(schema)
        if fb is not None:
            return fb
        return _placeholder("列出表")
    service_name = str(service or omd_service()).strip()
    schema_fqn = urllib.parse.quote(f"{service_name}.{database or omd_database()}.{schema}")
    tables = _paginate(f"/tables?databaseSchema={schema_fqn}")
    if not tables and not _api_get(f"/tables?databaseSchema={schema_fqn}&limit=1"):
        return {"ok": False, "error": "OpenMetadata 查询失败（token 可能过期）"}
    return {
        "ok": True,
        "service": service_name,
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


def search_tables(
    table_name: str,
    service: str | None = None,
    database: str | None = None,
    schema: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """跨 OMD 可见 Service 搜索表，避免在缺少上下文时猜测默认库。"""
    query = str(table_name or "").strip()
    if not query:
        return {"ok": False, "error": "table_name 不能为空"}
    if not omd_configured():
        return _placeholder("搜索表")

    safe_limit = min(max(int(limit or 20), 1), 50)
    params = urllib.parse.urlencode({
        "q": query,
        "index": "table",
        "from": "0",
        "size": str(safe_limit),
    })
    data = _api_get(f"/search/query?{params}")
    if not data:
        return {"ok": False, "error": "OpenMetadata 表搜索失败（网络或 token 过期）"}

    raw_hits = data.get("hits") or data.get("data") or []
    if isinstance(raw_hits, dict):
        raw_hits = raw_hits.get("hits") or raw_hits.get("data") or []
    candidates: list[dict[str, Any]] = []
    for hit in raw_hits if isinstance(raw_hits, list) else []:
        source = hit.get("_source") or hit.get("entity") or hit
        fqn = str(source.get("fullyQualifiedName") or source.get("fqn") or "")
        parts = fqn.split(".")
        item = {
            "name": source.get("name") or (parts[-1] if parts else ""),
            "fqn": fqn,
            "service": source.get("serviceName") or (parts[0] if len(parts) >= 4 else ""),
            "database": source.get("databaseName") or (parts[-3] if len(parts) >= 4 else ""),
            "schema": source.get("schemaName") or (parts[-2] if len(parts) >= 4 else ""),
            "description": (source.get("description") or "")[:200],
        }
        if service and item["service"] != service:
            continue
        if database and item["database"] != database:
            continue
        if schema and item["schema"] != schema:
            continue
        if item["name"]:
            candidates.append(item)
    return {"ok": True, "query": query, "total": len(candidates), "candidates": candidates}


def _pg_fallback_tables(schema: str) -> dict[str, Any] | None:
    """PG information_schema 兜底：列某 schema 的表。DSN 不可用返回 None。"""
    dsn = os.getenv("DATADECK_SQL_DSN", "")
    if not dsn or not dsn.startswith(("postgresql://", "postgres://")):
        return None
    try:
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

        rows = _run_async_query(query)
        if rows is None:
            return None
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

        rows = _run_async_query(query)
        if rows is None:
            return None
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


def get_table_schema(schema: str, table: str, database: str | None = None, service: str | None = None) -> dict[str, Any]:
    """表结构：字段名/类型/注释（Text2SQL 的核心弹药）。"""
    if not omd_configured():
        fb = _pg_fallback_table_schema(schema, table)
        if fb is not None:
            return fb
        return _placeholder("查询表结构")
    service_name = str(service or omd_service()).strip()
    table_fqn = urllib.parse.quote(
        f"{service_name}.{database or omd_database()}.{schema}.{table}")
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
        "service": service_name,
        "table": table,
        "schema": schema,
        "description": (data.get("description") or "")[:300],
        "column_count": len(columns),
        "columns": columns,
    }


def get_table_lineage(
    schema: str, table: str, depth: int = 1, direction: str = "both",
    database: str | None = None, service: str | None = None,
) -> dict[str, Any]:
    if not omd_configured():
        return _placeholder("查询血缘")
    up_depth = depth if direction in ("both", "up") else 0
    down_depth = depth if direction in ("both", "down") else 0
    service_name = str(service or omd_service()).strip()
    fqn = urllib.parse.quote(f"{service_name}.{database or omd_database()}.{schema}.{table}")
    data = _api_get(
        f"/lineage/getLineage?fqn={fqn}&upstreamDepth={up_depth}"
        f"&downstreamDepth={down_depth}&type=table"
    )
    if not data:
        return {"ok": False, "error": "血缘查询失败（表不存在或 token 过期）"}

    def _fqn(entity: dict[str, Any]) -> str:
        return str(entity.get("fullyQualifiedName") or entity.get("fqn") or "").strip()

    target_fqn = f"{service_name}.{database or omd_database()}.{schema}.{table}"
    upstream: list[str] = []
    downstream: list[str] = []

    # OpenMetadata 现网响应使用顶层 edges，并用 fqn 表示实体；旧版本响应
    # 才使用 upstreamEdges/downstreamEdges。以目标表方向判定，避免丢失真实血缘。
    graph_up: dict[str, list[str]] = {}
    graph_down: dict[str, list[str]] = {}
    for edge in data.get("edges") or []:
        source = _fqn(edge.get("fromEntity") or {})
        target = _fqn(edge.get("toEntity") or {})
        if source and target:
            graph_up.setdefault(target, []).append(source)
            graph_down.setdefault(source, []).append(target)

    def _walk(graph: dict[str, list[str]]) -> list[str]:
        found: list[str] = []
        frontier = [target_fqn]
        visited = {target_fqn}
        for _ in range(max(int(depth or 1), 1)):
            next_frontier: list[str] = []
            for current in frontier:
                for neighbor in graph.get(current, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        found.append(neighbor)
                        next_frontier.append(neighbor)
            frontier = next_frontier
            if not frontier:
                break
        return found

    if direction in ("both", "up"):
        upstream = _walk(graph_up)
    if direction in ("both", "down"):
        downstream = _walk(graph_down)

    if not upstream:
        upstream = [
            _fqn(e.get("fromEntity") or {})
            for e in (data.get("upstreamEdges") or [])
            if _fqn(e.get("fromEntity") or {})
        ]
    if not downstream:
        downstream = [
            _fqn(e.get("toEntity") or {})
            for e in (data.get("downstreamEdges") or [])
            if _fqn(e.get("toEntity") or {})
        ]
    return {
        "ok": True,
        "service": service_name,
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
