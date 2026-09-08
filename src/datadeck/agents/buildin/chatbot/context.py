"""ChatBotContext：内置对话智能体上下文（自 Yuxi 抽离，去掉 subagents 平台资源）。"""

from __future__ import annotations

from dataclasses import dataclass

from datadeck.agents.context import BaseContext


@dataclass(kw_only=True)
class ChatBotContext(BaseContext):
    """text2sql 自检/反思配置（能力开关走结构化 context，不进自由文本）。"""

    sql_guard_enabled: bool = False
    sql_dialect: str = "doris"
    sql_max_reflect: int = 2
    sql_max_tables: int = 8
    sql_max_joins: int = 6