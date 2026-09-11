"""ChatBot 智能体：核心环组装（自 Yuxi agents/buildin/chatbot/graph.py 抽离）。

保留：create_agent + middleware 链（summary/memory/todo/model retry/token/steer/approval）。
去掉：sandbox/knowledge/subagent/skills/image 门控中间件与平台 backend。
summary 使用 langchain 公开 SummarizationMiddleware（无需专用 backend）。
"""

from __future__ import annotations

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware, TodoListMiddleware

from datadeck.agents.base import BaseAgent
from datadeck.agents.buildin.chatbot.context import ChatBotContext
from datadeck.agents.buildin.chatbot.data_prompt import build_data_agent_prompt
from datadeck.agents.buildin.chatbot.prompt import TODO_MID_PROMPT, build_prompt_with_context
from datadeck.agents.buildin.chatbot.state import ChatBotState
from datadeck.agents.context import BaseContext
from datadeck.agents.middlewares import TokenUsageMiddleware
from datadeck.agents.middlewares.data_selfcheck import create_data_selfcheck_middleware
from datadeck.agents.middlewares.data_workflow import DataWorkflowMiddleware
from datadeck.agents.middlewares.memory import create_memory_middleware
from datadeck.agents.middlewares.sql_selfcheck import create_sql_selfcheck_middleware
from datadeck.agents.middlewares.summary import create_summary_middleware
from datadeck.agents.middlewares.token_budget import TokenBudgetMiddleware
from datadeck.agents.models import default_model_spec, load_chat_model
from datadeck.agents.tool_approval import create_tool_approval_middleware, normalize_tool_approval_mode
from datadeck.agents.toolkits.service import resolve_configured_runtime_tools
from datadeck.ports.checkpointer import CheckpointerProvider
from datadeck.ports.memory import MemoryStore


async def _build_middlewares(context: BaseContext, *, model, memory_store: MemoryStore | None = None):
    """构建 middleware 链（datadeck 版）。"""
    summary_middleware = create_summary_middleware(
        model,
        trigger_k=getattr(context, "summary_threshold", None),
        keep_messages=getattr(context, "summary_keep_messages", None),
        summary_prompt=getattr(context, "summary_prompt", None),
    )

    middlewares = []
    sql_selfcheck_middleware = create_sql_selfcheck_middleware(context)
    if sql_selfcheck_middleware:
        middlewares.append(sql_selfcheck_middleware)
    data_selfcheck_middleware = create_data_selfcheck_middleware(context)
    if data_selfcheck_middleware:
        middlewares.append(data_selfcheck_middleware)
    if bool(getattr(context, "data_workflow_enabled", False)):
        middlewares.append(DataWorkflowMiddleware())
    memory_middleware = await create_memory_middleware(context, store=memory_store)
    if memory_middleware:
        middlewares.append(memory_middleware)
    middlewares.extend([
        summary_middleware,
        TokenBudgetMiddleware(),
        TodoListMiddleware(system_prompt=TODO_MID_PROMPT),
        PatchToolCallsMiddleware(),
        ModelRetryMiddleware(max_retries=int(getattr(context, "model_retry_times", 2))),
        TokenUsageMiddleware(),
    ])
    approval_middleware = create_tool_approval_middleware(
        normalize_tool_approval_mode(getattr(context, "tool_approval_mode", "default")),
        current_project_path=getattr(context, "workdir_path", None),
    )
    if approval_middleware:
        middlewares.append(approval_middleware)
    return middlewares


class ChatbotAgent(BaseAgent):
    name = "datadeck 助手"
    description = "基础的对话机器人，可回答问题，可在配置中启用需要的工具。"
    capabilities = ["workspace_files"]
    context_schema = ChatBotContext

    def __init__(self, *, model_provider, memory_store: MemoryStore | None = None,
                 checkpointer_provider: CheckpointerProvider | None = None, **kwargs):
        super().__init__(checkpointer_provider=checkpointer_provider, **kwargs)
        self._model_provider = model_provider
        self._memory_store = memory_store
        self._context_skills_resolver = kwargs.get("context_skills_resolver")
        self._extra_middlewares = kwargs.get("extra_middlewares")

    async def get_graph(self, context=None, **kwargs):
        context = context or self.context_schema()
        if self._context_skills_resolver is not None:
            await self._context_skills_resolver(context)
        else:
            await sync_agent_context_skills(context)
        model_spec = getattr(context, "model", "") or ""
        if not model_spec:
            model_spec = default_model_spec(self._model_provider)
            context.model = model_spec
        model = load_chat_model(model_spec, provider=self._model_provider)

        middlewares = await _build_middlewares(
            context, model=model, memory_store=self._memory_store)
        if self._extra_middlewares is not None:
            middlewares.extend(await self._extra_middlewares(context))

        tools = await resolve_configured_runtime_tools(context)
        # 宿主已完成 Skill 授权后，把其本地依赖注册进 ToolNode；中间件只负责按需隐藏/展示。
        tool_names = {tool.name for tool in tools}
        for skill_tool in getattr(context, "_skill_tool_instances", []) or []:
            if skill_tool.name not in tool_names:
                tools.append(skill_tool)
                tool_names.add(skill_tool.name)
        for platform_tool in getattr(context, "_platform_tools", []) or []:
            if platform_tool.name not in tool_names:
                tools.append(platform_tool)
                tool_names.add(platform_tool.name)
        system_prompt = build_prompt_with_context(context)
        if any(getattr(t, "name", "").startswith(("sql_execute", "omd_", "rag_")) for t in tools):
            system_prompt = build_data_agent_prompt(system_prompt)
        collection = getattr(context, "knowledge_base_collection", None)
        if collection:
            system_prompt += (
                "\n\n当前会话已选择知识库。调用 rag_search 时必须传入 "
                f'collection_name="{collection}"，不得检索其他知识库。'
            )

        return create_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            middleware=middlewares,
            state_schema=ChatBotState,
            context_schema=ChatBotContext,
            checkpointer=await self._get_checkpointer(),
        )


async def sync_agent_context_skills(context) -> None:
    """datadeck 无技能平台依赖；保留 no-op 以对齐 Yuxi 组装接口。"""
    return None
