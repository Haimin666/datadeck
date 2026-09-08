# 数据工具集：SQL 执行 / OMD 元数据 / RAG 检索（企业 DataAgent 三件套）。
# 执行层在 sql_executor.py / omd_client.py / rag_store.py，本模块做 @tool 注册 + 熔断接入。

import asyncio as _asyncio

from datadeck.agents.toolkits.registry import tool


async def _sync(fn, *args, **kwargs):
    """同步执行层函数放线程池，避免阻塞事件循环。"""
    return await _asyncio.to_thread(fn, *args, **kwargs)



# ── SQL 执行 ──────────────────────────────────────────────

@tool(category="data", tags=["SQL", "执行", "查询"], display_name="SQL 只读查询",
      description="执行一条只读 SELECT SQL 并返回真实数据。用于：用户明确要【查数据/取数/统计数值】时。"
                  "自动经过只读校验（禁 DML/DDL）与行数限制。生成 SQL 前应先用 omd 工具确认表结构。")
async def sql_execute_query(sql: str) -> dict:
    """Execute a read-only SQL query against the enterprise data warehouse.
    Returns columns, rows, row_count. The SQL must be a single read-only SELECT."""
    from datadeck.agents.toolkits.circuit_breaker import aguard
    from datadeck.agents.toolkits.sql_executor import execute_readonly_sql

    return await aguard("sql_execute_query", lambda: execute_readonly_sql(sql))


# ── OMD 元数据 ────────────────────────────────────────────

@tool(category="data", tags=["元数据", "表结构"], display_name="列出库与 Schema",
      description="列出企业数仓的所有数据库和 Schema。用于：用户问【有哪些库/有哪些主题域】时。")
async def omd_list_databases() -> dict:
    """List all databases and schemas in the enterprise data warehouse (via OpenMetadata)."""
    from datadeck.agents.toolkits.omd_client import list_databases, list_schemas

    dbs = list_databases()
    if dbs.get("ok"):
        schemas = list_schemas()
        dbs["schemas"] = schemas.get("schemas", []) if schemas.get("ok") else []
    return dbs


@tool(category="data", tags=["元数据", "表清单"], display_name="列出 Schema 下的表",
      description="列出指定 Schema 下的全部表（含表描述）。用于：用户问【XX 主题域有哪些表】或需要浏览表清单时。")
async def omd_list_tables(schema_name: str) -> dict:
    """List all tables in a schema (with descriptions) via OpenMetadata."""
    from datadeck.agents.toolkits.cache import cached_meta_async
    from datadeck.agents.toolkits.circuit_breaker import aguard
    from datadeck.agents.toolkits.omd_client import list_tables

    async def _run() -> dict:
        return await aguard("omd_list_tables", lambda: _sync(list_tables, schema_name))

    return await cached_meta_async(("omd_list_tables", schema_name), _run)


@tool(category="data", tags=["元数据", "表结构", "Text2SQL"], display_name="查询表结构",
      description="查询某张表的完整结构：字段名、类型、字段注释。"
                  "【写 SQL 前必须先用本工具确认表结构】，禁止凭空猜测表名字段名。")
async def omd_get_table_schema(schema_name: str, table: str) -> dict:
    """Get full column schema (name/type/comment) of a table via OpenMetadata."""
    from datadeck.agents.toolkits.cache import cached_meta_async
    from datadeck.agents.toolkits.circuit_breaker import aguard
    from datadeck.agents.toolkits.omd_client import get_table_schema

    async def _run() -> dict:
        return await aguard(
            "omd_get_table_schema", lambda: _sync(get_table_schema, schema_name, table))

    return await cached_meta_async(("omd_get_table_schema", schema_name, table), _run)


@tool(category="data", tags=["元数据", "血缘"], display_name="查询表血缘",
      description="查询某张表的上游/下游血缘依赖。用于：用户问【这张表数据从哪来/下游谁在用/血缘】时。"
                  "direction: up=上游, down=下游, both=全部。")
async def omd_get_table_lineage(
    schema_name: str, table: str, direction: str = "both", depth: int = 1
) -> dict:
    """Get upstream/downstream lineage of a table via OpenMetadata."""
    from datadeck.agents.toolkits.circuit_breaker import aguard
    from datadeck.agents.toolkits.omd_client import get_table_lineage

    return await aguard("omd_get_table_lineage", lambda: _sync(get_table_lineage, schema_name, table, depth=depth, direction=direction))


# ── RAG 检索 ──────────────────────────────────────────────

@tool(category="data", tags=["RAG", "知识库", "指标口径"], display_name="知识库检索",
      description="检索企业指标口径/业务知识文档。用于：用户问【指标定义/口径/计算公式/业务概念】时，"
                  "例如'逾期率怎么算'。返回相关文档片段与来源。domain 可选：限定业务域（如 credit/risk）。")
async def rag_search(query: str, top_k: int = 5, domain: str = "") -> dict:
    """Search the enterprise knowledge base (metric definitions & business docs) by hybrid retrieval.
    domain: optional business domain filter, empty string means all domains."""
    from datadeck.agents.toolkits.circuit_breaker import aguard
    from datadeck.agents.toolkits.rag_store import search

    return await aguard("rag_search", lambda: _sync(search, query, top_k=top_k, domain=domain or None))
