"""DataAgent：独立于通用对话 Agent 的企业数据分析智能体。"""

from __future__ import annotations

from datadeck.agents.buildin.chatbot.graph import ChatbotAgent


DATA_AGENT_TOOLS = [
    "metric_lookup",
    "rag_search",
    "omd_list_databases",
    "omd_list_tables",
    "omd_get_table_schema",
    "omd_get_table_lineage",
    "sql_validate",
    "sql_execute_query",
]


class DataAgent(ChatbotAgent):
    """固定开启数据工具和 SQL 自检，通用 ChatbotAgent 不受影响。"""

    name = "数据分析助手"
    description = "按知识库口径、元数据和只读 SQL 完成企业数据分析。"
    capabilities = ["knowledge", "metadata", "sql"]

    async def get_graph(self, context=None, **kwargs):
        context = context or self.context_schema()
        configured_tools = getattr(context, "tools", None)
        if configured_tools is None:
            context.tools = list(DATA_AGENT_TOOLS)
        else:
            # DataAgent 的数据能力不能被通用工具白名单误删；允许宿主
            # 额外添加工具，但始终保留 RAG、OMD 和只读 SQL。
            context.tools = list(dict.fromkeys([*DATA_AGENT_TOOLS, *configured_tools]))
        context.sql_guard_enabled = True
        context.data_workflow_enabled = True
        return await super().get_graph(context=context, **kwargs)
