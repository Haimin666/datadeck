"""DataAgent：独立于通用对话 Agent 的企业数据分析智能体。"""

from __future__ import annotations

from datadeck.agents.buildin.chatbot.graph import ChatbotAgent
from datadeck.agents.policy import DATA_AGENT_POLICY


DATA_AGENT_TOOLS = list(DATA_AGENT_POLICY.fixed_tool_selection)


class DataAgent(ChatbotAgent):
    """固定开启数据工具和 SQL 自检，通用 ChatbotAgent 不受影响。"""

    name = "数据分析助手"
    description = "按知识库口径、元数据和只读 SQL 完成企业数据分析。"
    capabilities = ["knowledge", "metadata", "sql"]
    policy = DATA_AGENT_POLICY

    async def get_graph(self, context=None, **kwargs):
        context = context or self.context_schema()
        if kwargs.get("metadata_only") and not getattr(context, "_runtime_prepared", False):
            # 历史/状态读取只构建无用户资源的元信息图；保留显式模式，
            # 让父类不会把它误判为绕过正式运行时装配。
            context._runtime_mode = "metadata"
        configured_tools = getattr(context, "tools", None)
        # DataAgent 的数据能力不能被通用工具白名单误删；允许宿主额外添加工具，
        # 但始终保留 RAG、OMD 和只读 SQL。
        context.tools = self.policy.merge_tools(configured_tools)
        context.sql_guard_enabled = True
        context.data_workflow_enabled = True
        return await super().get_graph(context=context, **kwargs)
