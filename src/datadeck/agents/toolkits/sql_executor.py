"""企业数据源执行层：env 可配 DSN（PG 测试 / Doris 生产），只读单条，自动 LIMIT。

- DATADECK_SQL_DSN: psycopg 风格 DSN，如 postgresql://lbc@localhost:5432/datadeck
                     生产 Doris: mysql://user:pass@doris-host:9030/db
- DATADECK_SQL_DIALECT: sqlglot 方言（postgresql / doris），默认从 DSN 推断
- DATADECK_SQL_MAX_ROWS: 最大返回行数（默认 200，强制包裹 LIMIT）
"""

from __future__ import annotations

import asyncio
import os
import urllib.parse
from typing import Any

from datadeck import logger
from datadeck.agents.sql_guard import validate_sql

DEFAULT_MAX_ROWS = 200

_dialect_by_scheme = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "doris": "doris",
}


def sql_dsn() -> str:
    return os.getenv("DATADECK_SQL_DSN", "")


def sql_dialect() -> str:
    dsn = sql_dsn()
    if not dsn:
        return "postgres"
    scheme = dsn.split(":", 1)[0]
    return _dialect_by_scheme.get(scheme, scheme)


def sql_max_rows() -> int:
    try:
        return max(1, int(os.getenv("DATADECK_SQL_MAX_ROWS", str(DEFAULT_MAX_ROWS))))
    except ValueError:
        return DEFAULT_MAX_ROWS


def _inject_limit(sql: str, max_rows: int, dialect: str) -> str:
    """给无 LIMIT 的 SELECT 注入/压低 LIMIT（尽力而为，失败原样返回交由 DB 报错）。"""
    try:
        import sqlglot
        from sqlglot import exp

        ast = sqlglot.parse_one(sql, read=dialect)
        if not isinstance(ast, exp.Select):
            return sql
        for limit in ast.find_all(exp.Limit):
            try:
                literal = int(str(limit.args.get("expression")))
                if literal > max_rows:
                    limit.set("expression", exp.Literal.number(max_rows))
                return ast.sql(dialect=dialect)
            except (TypeError, ValueError):
                return sql  # 动态 LIMIT（占位符/表达式）不动
        # 无 LIMIT：直接注入
        ast.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        return ast.sql(dialect=dialect)
    except Exception:  # noqa: BLE001
        return sql


def _rows_to_payload(rows: list, columns: list[str], truncated: bool) -> dict[str, Any]:
    max_rows = sql_max_rows()
    data = rows[:max_rows]
    return {
        "ok": True,
        "columns": columns,
        "row_count": len(data),
        "rows": [list(r) for r in data],
        "truncated": truncated or len(rows) > len(data),
        "limit": max_rows,
    }


def sql_explain_cost_limit() -> int:
    """EXPLAIN 预估行数超过该值即拒绝（默认 5000 万行）。"""
    try:
        return max(1, int(os.getenv("DATADECK_SQL_EXPLAIN_MAX_ROWS", "50000000")))
    except ValueError:
        return 50_000_000


async def _explain_estimated_rows(final_sql: str) -> int | None:
    """EXPLAIN 预估行数；不支持/失败返回 None（放行，交由超时兜底）。PG: EXPLAIN JSON。"""
    if sql_dialect() != "postgres":
        return None
    import asyncpg

    dsn = sql_dsn()
    conn = await asyncpg.connect(
        dsn.replace("postgresql://", "postgres://", 1) if dsn.startswith("postgresql://") else dsn
    )
    try:
        val = await conn.fetchval(f"EXPLAIN (FORMAT JSON) {final_sql}")
        import json

        plan = json.loads(val)[0]["Plan"]
        return int(plan.get("Plan Rows") or 0)
    except Exception:  # noqa: BLE001
        return None
    finally:
        await conn.close()


async def execute_readonly_sql(sql: str) -> dict[str, Any]:
    """校验 + EXPLAIN 预判 + 只读执行一条 SQL，返回结构化结果；任何失败返回 ok=False（工具侧永不抛异常）。"""
    if not sql_dsn():
        return {
            "ok": False,
            "error": "SQL 数据源未配置（DATADECK_SQL_DSN），当前为占位状态，请联系管理员配置数据源。",
            "hint": "占位模式下不执行任何查询。",
        }

    dialect = sql_dialect()
    check = validate_sql(sql, dialect=dialect)
    if not check.ok:
        return {
            "ok": False,
            "error": "SQL 未通过只读校验，拒绝执行。",
            "issues": [i.to_payload() for i in check.issues],
        }

    final_sql = _inject_limit(check.normalized_sql or sql, sql_max_rows(), dialect)

    # 大表扫描预判：EXPLAIN 估算行数超阈值直接拒绝（金融场景宁可保守）
    est_rows = await _explain_estimated_rows(final_sql)
    if est_rows is not None and est_rows > sql_explain_cost_limit():
        return {
            "ok": False,
            "error": f"查询被安全策略拒绝：EXPLAIN 预估扫描 {est_rows} 行，"
                     f"超过上限 {sql_explain_cost_limit()}，可能拖垮集群。请增加过滤条件（如时间分区/分区键）后重试。",
            "estimated_rows": est_rows,
            "executed_sql": final_sql,
        }

    try:
        rows, columns = await _run_query(final_sql)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"SQL 执行失败: {type(exc).__name__}: {str(exc)[:200]}")
        return {
            "ok": False,
            "error": f"SQL 执行失败: {str(exc)[:300]}",
            "executed_sql": final_sql,
        }

    return {
        **_rows_to_payload(rows, columns, False),
        "executed_sql": final_sql,
        **({"estimated_rows": est_rows} if est_rows is not None else {}),
    }


async def _run_query(final_sql: str) -> tuple[list, list[str]]:
    scheme = sql_dsn().split(":", 1)[0]
    if scheme in ("postgresql", "postgres"):
        return await _run_postgres(final_sql)
    if scheme in ("mysql", "doris"):
        return await _run_mysql(final_sql)
    raise RuntimeError(f"不支持的 DSN scheme: {scheme}（支持 postgresql/mysql）")


async def _run_postgres(final_sql: str) -> tuple[list, list[str]]:
    import asyncpg

    dsn = sql_dsn()
    # datadeck 主库复用同一实例，直接连；无密码本地 socket/localhost 常见
    conn = await asyncpg.connect(dsn.replace("postgresql://", "postgres://", 1) if dsn.startswith("postgresql://") else dsn)
    try:
        rows = await conn.fetch(final_sql)
        columns = [str(desc) for desc in (rows[0].keys() if rows else [])]
        return [tuple(r) for r in rows], columns
    finally:
        await conn.close()


async def _run_mysql(final_sql: str) -> tuple[list, list[str]]:
    try:
        import aiomysql
    except ImportError as exc:
        raise RuntimeError("Doris(MySQL 协议) 执行需要 aiomysql：uv pip install aiomysql") from exc

    parsed = urllib.parse.urlparse(sql_dsn())
    conn = await aiomysql.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 9030,
        user=parsed.username or "root",
        password=parsed.password or "",
        db=(parsed.path or "/").lstrip("/") or "default",
    )
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(final_sql)
            rows = await cur.fetchall()
            data = [tuple(r.values()) for r in rows]
            columns = list(rows[0].keys()) if rows else [d[0] for d in cur.description or []]
            return data, columns
    finally:
        conn.close()


async def test_connection() -> dict[str, Any]:
    """连通性自检（诊断/验收用）。"""
    if not sql_dsn():
        return {"ok": False, "error": "DATADECK_SQL_DSN 未配置"}
    try:
        rows, cols = await _run_query("SELECT 1")
        return {"ok": True, "sample": {cols[0] if cols else "?": rows[0][0] if rows else None}}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
