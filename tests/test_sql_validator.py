"""SqlValidator（确定性只读 SQL 校验器）单元测试。"""

import pytest

from datadeck.agents.sql_guard import validate_sql


class TestParse:
    def test_valid_select_passes(self):
        r = validate_sql("SELECT id, name FROM users WHERE id = 1", dialect="doris")
        assert r.ok, f"应通过: {r.issues}"
        assert r.normalized_sql
        assert "users" in r.tables

    def test_empty_sql_rejected(self):
        for bad in ("", "   ", None):
            r = validate_sql(bad, dialect="doris")
            assert not r.ok
            assert r.issues[0].code == "SQL_PARSE_EMPTY"

    def test_broken_sql_rejected(self):
        # 真正不可解析的输入（sqlglot 对残缺 SQL 宽容，参考 AGENTS.md 经验）
        r = validate_sql("SELECT FROM WHERE (((", dialect="doris")
        assert not r.ok
        assert r.issues[0].code == "SQL_PARSE_ERROR"

    def test_normalized_sql_prefers_dialect(self):
        r = validate_sql("SELECT a FROM t", dialect="doris")
        assert r.normalized_sql is not None
        assert "SELECT" in r.normalized_sql.upper()


class TestReadonly:
    def test_insert_rejected(self):
        r = validate_sql("INSERT INTO t VALUES (1)", dialect="doris")
        assert not r.ok
        assert any(i.code.startswith("SQL_FORBIDDEN") for i in r.issues)

    def test_update_rejected(self):
        r = validate_sql("UPDATE t SET a = 1", dialect="doris")
        assert not r.ok
        assert any(i.code.startswith("SQL_FORBIDDEN") for i in r.issues)

    def test_delete_rejected(self):
        r = validate_sql("DELETE FROM t", dialect="doris")
        assert not r.ok
        assert any(i.code.startswith("SQL_FORBIDDEN") for i in r.issues)

    def test_create_rejected(self):
        r = validate_sql("CREATE TABLE t (a INT)", dialect="doris")
        assert not r.ok
        assert any(i.code.startswith("SQL_FORBIDDEN") for i in r.issues)

    def test_drop_rejected(self):
        r = validate_sql("DROP TABLE t", dialect="doris")
        assert not r.ok
        assert any(i.code.startswith("SQL_FORBIDDEN") for i in r.issues)

    def test_select_into_rejected(self):
        r = validate_sql("SELECT * INTO new_t FROM old_t", dialect="doris")
        assert not r.ok
        assert any(i.code == "SQL_FORBIDDEN_SELECT_INTO" for i in r.issues)

    def test_multi_statement_rejected(self):
        r = validate_sql("SELECT 1; SELECT 2", dialect="doris")
        assert not r.ok
        assert any(i.code == "SQL_MULTI_STATEMENT" for i in r.issues)

    def test_non_query_rejected(self):
        # EXPLAIN 不含 SELECT 主体，视为非查询
        r = validate_sql("EXPLAIN SELECT 1", dialect="doris")
        assert not r.ok
        assert any(i.code == "SQL_NOT_A_QUERY" for i in r.issues)


class TestTables:
    def test_table_count_within_limit(self):
        sql = (
            "SELECT * FROM a JOIN b ON a.id = b.id "
            "JOIN c ON b.id = c.id JOIN d ON c.id = d.id"
        )
        r = validate_sql(sql, dialect="doris", max_tables=5)
        assert r.ok, r.issues
        assert len(r.tables) == 4

    def test_table_count_over_limit(self):
        sql = (
            "SELECT * FROM a JOIN b ON a.id = b.id "
            "JOIN c ON b.id = c.id JOIN d ON c.id = d.id "
            "JOIN e ON d.id = e.id JOIN f ON e.id = f.id"
        )
        r = validate_sql(sql, dialect="doris", max_tables=5)
        assert not r.ok
        assert any(i.code == "SQL_TOO_MANY_TABLES" for i in r.issues)

    def test_cte_references_not_counted_as_tables(self):
        sql = "WITH x AS (SELECT 1 AS v) SELECT * FROM x"
        r = validate_sql(sql, dialect="doris", max_tables=1)
        assert r.ok, f"CTE 引用不应计为物理表: {r.issues} tables={r.tables}"
        assert r.tables == []


class TestStructure:
    def test_union_of_selects_is_query(self):
        r = validate_sql("SELECT 1 UNION SELECT 2", dialect="doris")
        assert r.ok, r.issues

    def test_result_payload_shape(self):
        r = validate_sql("SELECT 1", dialect="doris")
        payload = r.to_payload()
        assert payload["ok"] is True
        assert payload["dialect"] == "doris"
        assert isinstance(payload["issues"], list)
        assert isinstance(payload["tables"], list)

    def test_issue_payload_shape(self):
        r = validate_sql("SELEC bad", dialect="doris")
        if not r.ok and r.issues:
            item = r.issues[0].to_payload()
            assert set(item.keys()) == {"code", "message"}
