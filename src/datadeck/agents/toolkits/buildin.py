# buildin 工具集合（datadeck 示例工具，验证 @tool 注册 + create_agent 工具绑定）。

from datadeck.agents.toolkits.registry import tool


@tool(category="buildin", tags=["示例"], display_name="回显", description="原样返回输入内容，用于验证工具绑定。")
def echo(text: str) -> str:
    """Echo the input text back."""
    return text


@tool(category="buildin", tags=["计算"], display_name="加法", description="两个整数相加。")
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@tool(category="sql", tags=["SQL", "校验"], display_name="SQL 只读校验",
      description="校验一条 SQL 是否为合法只读 SELECT 查询（语法/禁写/多语句/表连接数），"
                  "返回结构化问题清单。生成 SQL 后、写入回答前建议先调用自检。")
def sql_validate(sql: str, dialect: str = "doris") -> dict:
    """Validate that a SQL statement is a read-only single query."""
    from datadeck.agents.sql_guard import validate_sql

    return validate_sql(sql, dialect=dialect).to_payload()