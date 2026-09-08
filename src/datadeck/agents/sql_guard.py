"""SqlValidator: 确定性只读 SQL 校验器（无 LLM 参与，self-reflection 的"裁判"）。

职责（全部来自 datadeck 的使用场景约束）：
- 只允许单条纯查询（SELECT/UNION/CTE/EXPLAIN 包裹的 SELECT）
- 禁 DML/DDL/多语句/SELECT INTO —— text2sql 仅生成不执行
- 解析失败给出结构化错误反馈（供模型修正）
- 收集表清单（CTE 别名不算物理表）与连接数，超限告警

失败返回结构化 issues，绝不抛异常——中间件/工具把 issues 转成模型可读的反馈。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, SchemaError, UnsupportedError

__all__ = [
    "SqlIssue",
    "SqlValidationResult",
    "validate_sql",
    "DEFAULT_DIALECT",
    "DEFAULT_MAX_TABLES",
    "DEFAULT_MAX_JOINS",
]

DEFAULT_DIALECT = "doris"
DEFAULT_MAX_TABLES = 8
DEFAULT_MAX_JOINS = 6

_SELECT_LIKE = (exp.Select, exp.Union)


@dataclass
class SqlIssue:
    """单条校验问题（code 稳定，message 面向模型）。"""

    code: str
    message: str

    def to_payload(self) -> dict:
        return {"code": self.code, "message": self.message}


@dataclass
class SqlValidationResult:
    ok: bool
    normalized_sql: str | None
    tables: list[str] = field(default_factory=list)
    issues: list[SqlIssue] = field(default_factory=list)
    dialect: str = DEFAULT_DIALECT

    def to_payload(self) -> dict:
        return {
            "ok": self.ok,
            "normalized_sql": self.normalized_sql,
            "tables": list(self.tables),
            "issues": [i.to_payload() for i in self.issues],
            "dialect": self.dialect,
        }


def _issue(code: str, message: str) -> SqlIssue:
    return SqlIssue(code=code, message=message)


def _collect_tables(ast) -> tuple[list[str], int]:
    """物理表清单（CTE 别名剔除）+ join 次数。"""
    cte_names: set[str] = set()
    for cte in ast.find_all(exp.CTE):
        alias = cte.alias
        if alias:
            cte_names.add(alias.lower())
    tables: list[str] = []
    for table in ast.find_all(exp.Table):
        name = table.name or ""
        if not name or name.lower() in cte_names:
            continue
        text = table.sql(dialect=DEFAULT_DIALECT) if table.db or table.catalog else name
        if text not in tables:
            tables.append(text)
    joins = len(list(ast.find_all(exp.Join)))
    return tables, joins


def validate_sql(
    sql: str | None,
    *,
    dialect: str = DEFAULT_DIALECT,
    max_tables: int = DEFAULT_MAX_TABLES,
    max_joins: int = DEFAULT_MAX_JOINS,
) -> SqlValidationResult:
    """校验一条 SQL 是否为合法只读查询。永不抛异常。"""
    issues: list[SqlIssue] = []

    if not sql or not str(sql).strip():
        return SqlValidationResult(
            ok=False, normalized_sql=None, issues=[_issue("SQL_PARSE_EMPTY", "SQL 为空")],
            dialect=dialect,
        )

    try:
        asts = sqlglot.parse(sql, read=dialect)
    except (ParseError, UnsupportedError, OptimizeError, SchemaError) as exc:
        msg = str(exc).replace("\n", " ")[:300]
        return SqlValidationResult(
            ok=False, normalized_sql=None,
            issues=[_issue("SQL_PARSE_ERROR", f"SQL 解析失败: {msg}")],
            dialect=dialect,
        )
    except Exception as exc:  # noqa: BLE001 — sqlglot 偶发非常规异常也要收敛为 issue
        msg = f"{type(exc).__name__}: {exc}"[:300]
        return SqlValidationResult(
            ok=False, normalized_sql=None,
            issues=[_issue("SQL_PARSE_ERROR", f"SQL 解析失败: {msg}")],
            dialect=dialect,
        )

    if not asts:
        return SqlValidationResult(
            ok=False, normalized_sql=None,
            issues=[_issue("SQL_PARSE_EMPTY", "SQL 为空")], dialect=dialect,
        )
    if len(asts) > 1:
        issues.append(_issue(
            "SQL_MULTI_STATEMENT",
            f"包含 {len(asts)} 条语句，只允许单条查询",
        ))

    root = asts[0]
    # 只允许纯查询：SELECT/UNION/CTE。区分两类拒绝：
    # 写/DDL 类 → SQL_FORBIDDEN_STATEMENT（明确禁写）；其余类型 → SQL_NOT_A_QUERY。
    # EXPLAIN 属于执行/预览阶段，不属于 text2sql 生成产物，同样拒绝。
    if isinstance(root, (exp.Select, exp.Union)):
        pass
    elif isinstance(root, (
        exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
        exp.Alter, exp.TruncateTable, exp.Command, exp.Grant,
    )):
        issues.append(_issue(
            "SQL_FORBIDDEN_STATEMENT",
            f"禁止 {type(root).__name__} 语句：text2sql 仅生成只读 SELECT 查询",
        ))
    else:
        issues.append(_issue(
            "SQL_NOT_A_QUERY",
            f"只允许只读 SELECT 查询（含 UNION/CTE），当前语句类型是 {type(root).__name__}",
        ))

    forbidden_nodes = list(root.find_all(
        exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
        exp.Alter, exp.TruncateTable, exp.Command, exp.Grant,
    )) if isinstance(root, (exp.Select, exp.Union)) else []
    if forbidden_nodes:
        kinds = sorted({type(n).__name__ for n in forbidden_nodes})
        issues.append(_issue(
            "SQL_FORBIDDEN_STATEMENT",
            f"包含禁止的语句类型: {', '.join(kinds)}；只允许只读 SELECT",
        ))

    if "into" in getattr(root, "args", {}):
        issues.append(_issue(
            "SQL_FORBIDDEN_SELECT_INTO",
            "禁止 SELECT INTO；text2sql 仅生成只读查询",
        ))

    tables: list[str] = []
    joins = 0
    if isinstance(root, (exp.Select, exp.Union)):
        tables, joins = _collect_tables(root)
        if len(tables) > max_tables:
            issues.append(_issue(
                "SQL_TOO_MANY_TABLES",
                f"涉及 {len(tables)} 张表（上限 {max_tables}），查询可能过于复杂或误选表",
            ))
        if joins > max_joins:
            issues.append(_issue(
                "SQL_TOO_MANY_JOINS",
                f"包含 {joins} 个 JOIN（上限 {max_joins}）",
            ))

    normalized = None
    if not issues:
        try:
            normalized = root.sql(dialect=dialect, pretty=True)
        except Exception:  # noqa: BLE001 — 规范化失败不影响通过结论
            normalized = None

    return SqlValidationResult(
        ok=not issues,
        normalized_sql=normalized,
        tables=tables,
        issues=issues,
        dialect=dialect,
    )
