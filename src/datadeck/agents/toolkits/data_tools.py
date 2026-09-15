# 数据工具集：SQL 执行 / OMD 元数据 / RAG 检索（企业 DataAgent 三件套）。
# 执行层在 sql_executor.py / omd_client.py / rag_store.py，本模块做 @tool 注册 + 熔断接入。
# 平台运行时由 server 装配器把 rag_search 绑定到 PostgreSQL 知识库；这里的
# rag_store 仅服务 datadeck 的独立 standalone 调用，不是平台事实存储。

import asyncio as _asyncio
from typing import Literal

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

@tool(category="data", tags=["元数据", "表结构", "Service"], display_name="列出库与 Schema",
      description="列出 OMD Token 可见的数据库 Service、数据库和 Schema；数据库类型支持 Hive、Doris、Mysql、Oracle。")
async def omd_list_databases(
    service_name: str = "",
    service_type: Literal["", "Hive", "Doris", "Mysql", "Oracle"] = "",
) -> dict:
    """列出数据库类 Service 下的数据库和 Schema。

    service_type 只能是 Hive、Doris、Mysql、Oracle；Looker 和 CustomDashboard
    是看板类 Service，不能用于数据库/Schema 查询。
    """
    from datadeck.agents.toolkits.omd_client import list_databases, list_schemas

    from datadeck.agents.toolkits.circuit_breaker import aguard

    dbs = await aguard("omd_list_databases", lambda: _sync(
        list_databases, service_name or None, service_type or None))
    if dbs.get("ok") and service_name:
        schemas = await aguard("omd_list_databases", lambda: _sync(
            list_schemas, None, service_name))
        dbs["schemas"] = schemas.get("schemas", []) if schemas.get("ok") else []
    return dbs


@tool(category="data", tags=["元数据", "Service", "Hive", "Doris", "Mysql", "Oracle", "Looker", "CustomDashboard"], display_name="列出 OMD Service",
      description="列出当前 Token 可见的数据库和看板 Service；支持 Hive、Doris、Mysql、Oracle、Looker、CustomDashboard，可按 service_type 筛选。返回 service_category 和 service_type。")
async def omd_list_services(
    service_type: Literal[
        "", "Hive", "Doris", "Mysql", "Oracle", "Looker", "CustomDashboard",
    ] = "",
) -> dict:
    """列出当前 Token 可见的 OMD Service。

    六类 Service Type：Hive、Doris、Mysql、Oracle（数据库类），以及
    Looker、CustomDashboard（看板类）。返回 Service 名称、真实类型和类别；
    不传类型时返回全部六类。
    """
    from datadeck.agents.toolkits.circuit_breaker import aguard
    from datadeck.agents.toolkits.omd_client import list_services

    return await aguard("omd_list_services", lambda: _sync(
        list_services, service_type or None))


@tool(category="data", tags=["元数据", "表清单"], display_name="列出 Schema 下的表",
      description="列出指定 Schema 下的全部表（含表描述）。用于：用户问【XX 主题域有哪些表】或需要浏览表清单时。")
async def omd_list_tables(schema_name: str, service_name: str = "", database_name: str = "") -> dict:
    """List all tables in a schema (with descriptions) via OpenMetadata."""
    from datadeck.agents.toolkits.cache import cached_meta_async
    from datadeck.agents.toolkits.circuit_breaker import aguard
    from datadeck.agents.toolkits.omd_client import list_tables

    async def _run() -> dict:
        return await aguard("omd_list_tables", lambda: _sync(
            list_tables, schema_name, database_name or None, service_name or None))

    return await cached_meta_async(("omd_list_tables", service_name, database_name, schema_name), _run)


@tool(category="data", tags=["元数据", "表搜索", "Service"], display_name="搜索表",
      description="跨 Hive/Doris/Mysql/Oracle Service 搜索表。用户只给表名、未给 Service/数据库/Schema 时必须优先使用。"
                  "命中多个候选时，先把候选返回给用户确认，禁止猜测默认 Service。")
async def omd_search_tables(
    table_name: str, service_name: str = "", database_name: str = "",
    schema_name: str = "", limit: int = 20,
    service_type: Literal["", "Hive", "Doris", "Mysql", "Oracle"] = "",
) -> dict:
    """跨数据库类 Service 搜索表。

    支持 Hive、Doris、Mysql、Oracle；Looker 和 CustomDashboard 不能作为表搜索
    数据源。未给 Service 时必须先让用户从命中候选中确认真实 Service。
    """
    from datadeck.agents.toolkits.circuit_breaker import aguard
    from datadeck.agents.toolkits.omd_client import search_tables

    return await aguard("omd_search_tables", lambda: _sync(
        search_tables, table_name, service_name or None, database_name or None,
        schema_name or None, limit, service_type or None))


@tool(category="data", tags=["元数据", "表结构", "Text2SQL"], display_name="查询表结构",
      description="查询某张表的完整结构：字段名、类型、字段注释。"
                  "【写 SQL 前必须先用本工具确认表结构】，禁止凭空猜测表名字段名。")
async def omd_get_table_schema(schema_name: str, table: str, service_name: str = "", database_name: str = "") -> dict:
    """Get full column schema (name/type/comment) of a table via OpenMetadata."""
    from datadeck.agents.toolkits.cache import cached_meta_async
    from datadeck.agents.toolkits.circuit_breaker import aguard
    from datadeck.agents.toolkits.omd_client import get_table_schema

    async def _run() -> dict:
        return await aguard(
            "omd_get_table_schema", lambda: _sync(
                get_table_schema, schema_name, table, database_name or None, service_name or None))

    return await cached_meta_async(("omd_get_table_schema", service_name, database_name, schema_name, table), _run)


@tool(category="data", tags=["元数据", "血缘"], display_name="查询表血缘",
      description="查询某张表的上游/下游血缘依赖。用于：用户问【这张表数据从哪来/下游谁在用/血缘】时。"
                  "direction: up=上游, down=下游, both=全部。")
async def omd_get_table_lineage(
    schema_name: str, table: str, direction: str = "both", depth: int = 1,
    service_name: str = "", database_name: str = ""
) -> dict:
    """Get upstream/downstream lineage of a table via OpenMetadata."""
    from datadeck.agents.toolkits.circuit_breaker import aguard
    from datadeck.agents.toolkits.omd_client import get_table_lineage

    return await aguard("omd_get_table_lineage", lambda: _sync(
        get_table_lineage, schema_name, table, depth=depth, direction=direction,
        database=database_name or None, service=service_name or None))


# ── RAG 检索 ──────────────────────────────────────────────

@tool(category="data", tags=["RAG", "知识库", "指标口径"], display_name="知识库检索",
      description="检索企业指标口径/业务知识文档。用于：用户问【指标定义/口径/计算公式/业务概念】时，"
                  "例如'逾期率怎么算'。返回相关文档片段与来源。domain 可选：限定业务域（如 credit/risk）。")
async def rag_search(query: str, top_k: int = 5, domain: str = "",
                     collection_name: str = "") -> dict:
    """Search the enterprise knowledge base (metric definitions & business docs) by hybrid retrieval.
    domain: optional business domain filter, empty string means all domains."""
    from datadeck.agents.toolkits.circuit_breaker import aguard
    from datadeck.agents.toolkits.rag_store import search

    top_k = min(max(int(top_k), 1), 20)
    return await aguard(
        "rag_search",
        lambda: _sync(search, query, top_k=top_k, domain=domain or None,
                      collection_name=collection_name or None),
    )
