"""ChatBot 智能体：核心环组装（自 Yuxi agents/buildin/chatbot/graph.py 抽离）。

保留：create_agent + middleware 链（summary/memory/todo/model retry/token/steer/approval）。
去掉：sandbox/knowledge/subagent/skills/image 门控中间件与平台 backend。
summary 使用 langchain 公开 SummarizationMiddleware（无需专用 backend）。

宿主相关资源和 middleware 必须由 Runtime Context 注入；核心不持有宿主回调。
"""

from __future__ import annotations

import hashlib
import json
import time

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware, TodoListMiddleware
from langchain_core.tools import StructuredTool

from datadeck.agents.base import BaseAgent
from datadeck.agents.buildin.chatbot.context import ChatBotContext
from datadeck.agents.buildin.chatbot.data_prompt import build_data_agent_prompt
from datadeck.agents.buildin.chatbot.prompt import (
    TODO_MID_PROMPT,
    build_capability_prompt,
    build_prompt_with_context,
)
from datadeck.agents.buildin.chatbot.state import ChatBotState
from datadeck.agents.context import BaseContext
from datadeck.agents.middlewares import TokenUsageMiddleware
from datadeck.agents.middlewares.data_selfcheck import create_data_selfcheck_middleware
from datadeck.agents.middlewares.data_workflow import DataWorkflowMiddleware
from datadeck.agents.middlewares.memory import create_memory_middleware
from datadeck.agents.middlewares.sql_selfcheck import create_sql_selfcheck_middleware
from datadeck.agents.middlewares.summary import create_summary_middleware
from datadeck.agents.middlewares.token_budget import TokenBudgetMiddleware
from datadeck.agents.middlewares.tool_timeout import ToolTimeoutMiddleware
from datadeck.agents.middlewares.tool_failure_guard import ToolFailureGuardMiddleware
from datadeck.agents.middlewares.trust_boundary import ToolResultTrustBoundaryMiddleware
from datadeck.agents.middlewares.context_budget import resolve_context_budget
from datadeck.agents.models import default_model_spec, load_chat_model
from datadeck.agents.tool_approval import create_tool_approval_middleware, normalize_tool_approval_mode
from datadeck.agents.toolkits.service import resolve_configured_runtime_tools
from datadeck.ports.checkpointer import CheckpointerProvider
from datadeck.ports.memory import MemoryStore


async def _build_middlewares(context: BaseContext, *, model, memory_store: MemoryStore | None = None):
    """构建 middleware 链（datadeck 版）。"""
    budget = resolve_context_budget(
        getattr(context, "model_context_window", None),
        getattr(context, "model_max_output_tokens", None),
    )
    context.context_budget = budget.to_dict()
    summary_middleware = create_summary_middleware(
        model,
        trigger_k=budget.summary_trigger // 1024,
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
    # 记忆是可选能力；基础运行时中间件不能依赖 MemoryStore 是否启用。
    # 否则 standalone 或未配置记忆的正式 Run 会静默丢失摘要、预算、重试、
    # token 统计和工具超时保护，最终表现为长对话变慢或永久卡住。
    middlewares.extend([
        summary_middleware,
        TokenBudgetMiddleware(
            budget_tokens=budget.soft_budget,
            hard_limit_tokens=budget.hard_limit,
        ),
        TodoListMiddleware(system_prompt=TODO_MID_PROMPT),
        PatchToolCallsMiddleware(),
        ModelRetryMiddleware(max_retries=int(getattr(context, "model_retry_times", 2))),
        TokenUsageMiddleware(),
        ToolFailureGuardMiddleware(),
        ToolTimeoutMiddleware(getattr(context, "tool_timeout_seconds", 120)),
        ToolResultTrustBoundaryMiddleware(),
    ])
    approval_middleware = create_tool_approval_middleware(
        normalize_tool_approval_mode(getattr(context, "tool_approval_mode", "default")),
        current_project_path=getattr(context, "workdir_path", None),
    )
    if approval_middleware:
        middlewares.append(approval_middleware)
    return middlewares


def _scope_knowledge_tool(tool, collections: list[str]):
    """把 RAG 工具限制在当前用户已授权的多个 collection 内。"""
    async def scoped_rag_search(
        query: str, top_k: int = 5, domain: str = "", collection_name: str = ""
    ):
        requested = [item.strip() for item in str(collection_name or "").split(",") if item.strip()]
        if requested and any(item not in collections for item in requested):
            return {"ok": False, "error": "指定知识库不在当前 Agent 授权范围内"}
        targets = requested or collections
        results = []
        for target in targets:
            result = await tool.ainvoke({
                "query": query, "top_k": top_k, "domain": domain,
                "collection_name": target,
            })
            if isinstance(result, dict):
                if result.get("ok") is False:
                    return result
                results.extend(result.get("results") or [])
        results.sort(key=lambda item: float(item.get("score", 0) or 0), reverse=True)
        return {"ok": True, "results": results[:min(max(int(top_k), 1), 20)],
                "strategy": "multi_collection" if len(targets) > 1 else "single_collection"}

    return StructuredTool.from_function(
        coroutine=scoped_rag_search,
        name=tool.name,
        description=tool.description,
    )


class ChatbotAgent(BaseAgent):
    name = "datadeck 助手"
    description = "基础的对话机器人，可回答问题，可在配置中启用需要的工具。"
    capabilities = ["workspace_files"]
    context_schema = ChatBotContext

    def __init__(self, *, model_provider, memory_store: MemoryStore | None = None,
                 checkpointer_provider: CheckpointerProvider | None = None):
        super().__init__(checkpointer_provider=checkpointer_provider)
        self._graph_cache = {}
        self._graph_cache_limit = 32
        self._graph_cache_ttl = 600.0
        self._model_provider = model_provider
        self._memory_store = memory_store

    async def get_graph(self, context=None, *, metadata_only: bool = False, **kwargs):
        if context is None:
            if not metadata_only:
                raise RuntimeError(
                    "正式 Agent 运行必须先经过 AgentRuntimeAssembler；"
                    "仅允许通过 metadata_only=True 构建无用户资源图"
                )
            context = self.context_schema()
            context._runtime_mode = "metadata"
        runtime_mode = getattr(context, "_runtime_mode", "")
        if not getattr(context, "_runtime_prepared", False) and runtime_mode not in {
            "standalone", "metadata"
        }:
            raise RuntimeError(
                "正式 Agent 运行必须使用 AgentRuntimeAssembler 生成 Runtime Context"
            )
        # 宿主已通过 AgentRuntimeAssembler 完成一次性资源装配；核心只消费
        # Context 中的运行时快照和 middleware，不再调用宿主 resolver/hook。
        model_spec = getattr(context, "model", "") or ""
        if not model_spec:
            model_spec = default_model_spec(self._model_provider)
            context.model = model_spec
        model_info = (
            self._model_provider.get_model_info(model_spec)
            if hasattr(self._model_provider, "get_model_info") else None
        )
        context.model_context_window = getattr(model_info, "context_window", None)
        context.model_max_output_tokens = getattr(model_info, "max_output_tokens", None)
        model = load_chat_model(model_spec, provider=self._model_provider)

        middlewares = await _build_middlewares(
            context, model=model, memory_store=self._memory_store)
        middlewares.extend(list(getattr(context, "runtime_middlewares", ()) or ()))

        if getattr(context, "_runtime_prepared", False):
            # 正式运行只消费宿主装配器生成的请求级工具快照。
            tools = list(getattr(context, "runtime_tools", ()) or ())
        else:
            # 仅供 standalone/metadata 建图使用；正式入口在上方已被拦截。
            tools = await resolve_configured_runtime_tools(context)
        knowledge_collections = [
            str(item) for item in getattr(context, "knowledge_base_collections", [])
            if str(item).strip()
        ]
        if knowledge_collections:
            tools = [
                _scope_knowledge_tool(tool, knowledge_collections)
                if getattr(tool, "name", "") == "rag_search" else tool
                for tool in tools
            ]
        for tool in tools:
            # handle_tool_error 不覆盖 Pydantic 参数校验；显式设置后，校验错误
            # 会作为 ToolMessage 返回给模型，而不是直接击穿工具节点。
            tool.handle_validation_error = lambda error: (
                f"工具参数校验失败：{error}。请根据工具 schema 补全并修正参数。"
            )
        runtime_snapshot = getattr(context, "_runtime_snapshot", None)
        selected_mcps = runtime_snapshot.selected("mcps") if runtime_snapshot else ()
        snapshot_identity = {}
        if runtime_snapshot is not None:
            snapshot_identity = runtime_snapshot.to_dict()
            # diagnostics 含本次装配耗时，属于观测数据，不应导致等价运行时配置缓存失效。
            snapshot_identity.pop("diagnostics", None)
        cache_payload = {
            "uid": getattr(context, "uid", ""),
            "thread_id": getattr(context, "thread_id", ""),
            "model": model_spec,
            "tools": sorted(tool.name for tool in tools),
            "skills": sorted(getattr(context, "_effective_skill_slugs", []) or []),
            "mcps": sorted(selected_mcps or ()),
            "collections": sorted(knowledge_collections),
            "approval": getattr(context, "tool_approval_mode", "default"),
            "workdir_path": getattr(context, "workdir_path", ""),
            "system_prompt": getattr(context, "system_prompt", ""),
            "routing_hint": getattr(context, "routing_hint", ""),
            "data_workflow": bool(getattr(context, "data_workflow_enabled", False)),
            "sql_guard": bool(getattr(context, "sql_guard_enabled", False)),
            "model_retry_times": getattr(context, "model_retry_times", 2),
            "tool_timeout_seconds": getattr(context, "tool_timeout_seconds", 120),
            "subagent_workflow": getattr(context, "subagent_workflow", None),
            "resource_snapshot": snapshot_identity,
        }
        cache_key = hashlib.sha256(json.dumps(
            cache_payload, sort_keys=True, ensure_ascii=True, default=str,
        ).encode()).hexdigest()
        # 平台工具和中间件可能绑定当前 run 的上下文；正式 AgentRun 不复用
        # 另一轮的 graph，避免 run_id/父任务/审批状态被旧闭包污染。无 run_id
        # 的只读建图（例如元信息检查）仍可使用有界缓存。
        cacheable = not bool(getattr(context, "run_id", None))
        cached = self._graph_cache.get(cache_key) if cacheable else None
        if cached is not None:
            cached_at, cached_graph = cached
            if time.monotonic() - cached_at < self._graph_cache_ttl:
                self.graph = cached_graph
                return cached_graph
            self._graph_cache.pop(cache_key, None)
        system_prompt = build_prompt_with_context(context)
        if getattr(context, "task_kind", "") == "sync":
            effective_skills = set(getattr(context, "_effective_skill_slugs", []) or [])
            if "dba" not in effective_skills:
                system_prompt += (
                    "\n\n当前请求是同步任务，但当前运行时未挂载 dba Skill。"
                    "禁止调用任何其他工具、猜测表结构或访问无关路径；直接向用户说明 dba Skill 未挂载，"
                    "并提示重新挂载/授权后重试。"
                )
        data_tools = any(
            str(getattr(t, "name", "") or "").startswith(
                ("sql_execute", "omd_", "rag_", "metric_", "code_search")
            )
            for t in tools
        )
        is_data_agent = getattr(context, "agent_backend_id", "") == "DataAgent"
        if data_tools and is_data_agent:
            system_prompt = build_data_agent_prompt(
                system_prompt,
                has_dba=("dba" in set(getattr(context, "_effective_skill_slugs", []) or [])
                         and any(str(getattr(t, "name", "") or "") == "run_skill_script" for t in tools)),
            )
        capability_prompt = build_capability_prompt(context, tools)
        if capability_prompt:
            system_prompt += f"\n\n{capability_prompt}"
        collection = getattr(context, "knowledge_base_collection", None)
        if collection:
            system_prompt += (
                "\n\n当前会话已选择授权知识库。调用 rag_search 时只能使用当前授权范围；"
                "不传 collection_name 时由运行时在全部授权知识库内检索，"
                "不得检索其他知识库。"
            )
        workflow = getattr(context, "subagent_workflow", None)
        if workflow and getattr(context, "delegation_enabled", False):
            nodes = workflow.get("nodes", []) if isinstance(workflow, dict) else []
            edges = workflow.get("edges", []) if isinstance(workflow, dict) else []
            node_lines = [
                f"- id={item.get('id')}; subagent_slug={item.get('subagent_slug')}; task={item.get('task_template')}"
                for item in nodes if isinstance(item, dict)
            ]
            edge_lines = [
                f"- {item.get('source')} -> {item.get('target')}"
                for item in edges if isinstance(item, dict)
            ]
            if node_lines:
                node_text = "\n".join(node_lines)
                edge_text = "\n".join(edge_lines) or "- 无，节点可并行"
                system_prompt += (
                    "\n\n已配置可视化子智能体工作流。面对需要协作的用户请求，优先调用 "
                    "subagent_orchestrate：为每个节点创建一个任务，subagent_slug 和 task_template 必须按下列配置，"
                    "并把当前用户请求补充到每项 task；每条连线转换成 target 的 depends_on。完成后综合结果回答。\n"
                    f"节点：\n{node_text}\n连线：\n{edge_text}"
                )

        graph = create_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            middleware=middlewares,
            state_schema=ChatBotState,
            context_schema=ChatBotContext,
            checkpointer=await self._get_checkpointer(),
        )
        if cacheable:
            self._graph_cache[cache_key] = (time.monotonic(), graph)
        self.graph = graph
        if len(self._graph_cache) > self._graph_cache_limit:
            self._graph_cache.pop(next(iter(self._graph_cache)))
        return graph
