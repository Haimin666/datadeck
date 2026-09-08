"""BaseAgent: 智能体基类（自 Yuxi agents/base.py 抽离，去除 subagent/v3 事件/平台依赖）。

核心能力：搭图(get_graph) + 一次调用(invoke_messages) + 值流式(stream_values)。
```
graph = CompiledStateGraph (langgraph)
checkpointer 经 CheckpointerProvider 注入（历史/恢复由宿主提供）。
```
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from datadeck.agents.context import DEFAULT_MAX_EXECUTION_STEPS
from datadeck.agents.context import BaseContext

from datadeck import logger


def _recursion_limit_from_context(context: BaseContext, default: int) -> int:
    value = getattr(context, "max_execution_steps", default)
    return int(value) if isinstance(value, int) and value > 0 else default


class BaseAgent:
    """定义一个基础 Agent，供各类 graph 继承。"""

    name = "base_agent"
    description = "base_agent"
    capabilities: list[str] = []
    context_schema: type[BaseContext] = BaseContext

    def __init__(self, *, checkpointer_provider=None, **kwargs):
        self.graph = None
        self._checkpointer_provider = checkpointer_provider
        self.checkpointer = None

    @property
    def module_name(self) -> str:
        return self.__class__.__module__.split(".")[-2]

    @property
    def id(self) -> str:
        return self.__class__.__name__

    async def get_info(self, include_configurable_items: bool = True,
                       user_role: str | None = None) -> dict:
        metadata = getattr(self, "metadata", {}) or {}
        configurable_items = (
            self.context_schema.get_configurable_items(user_role=user_role)
            if include_configurable_items else {}
        )
        return {
            "id": self.id,
            "name": getattr(self, "name", "Unknown"),
            "description": getattr(self, "description", "Unknown"),
            "metadata": metadata,
            "configurable_items": configurable_items,
            "capabilities": list(getattr(self, "capabilities", [])),
        }

    async def get_config(self) -> BaseContext:
        return self.context_schema()

    def _input_config(self, context: BaseContext, **kwargs) -> dict:
        config = {
            "configurable": {"thread_id": context.thread_id, "uid": context.uid},
            "recursion_limit": _recursion_limit_from_context(context, DEFAULT_MAX_EXECUTION_STEPS),
        }
        if callbacks := kwargs.get("callbacks"):
            config["callbacks"] = list(callbacks)
        if metadata := kwargs.get("metadata"):
            config["metadata"] = dict(metadata)
        if tags := kwargs.get("tags"):
            config["tags"] = list(tags)
        return config

    async def invoke_messages(self, messages: list[str], input_context=None, **kwargs):
        """一次性运行：输入消息列表，返回最终消息。

        messages 是 role/content 的 dict 列表（langchain messages），或消息对象。
        """
        context = self.context_schema()
        context.update_from_dict(input_context or {})
        graph = await self.get_graph(context=context)
        return await graph.ainvoke(
            {"messages": messages},
            context=context,
            config=self._input_config(context, **kwargs),
        )

    async def stream_values(self, messages: list[str], input_context=None, **kwargs):
        """值流式：逐轮产出 {messages: [...]}（自 Yuxi stream_values，忠于原样）。"""
        context = self.context_schema()
        context.update_from_dict(input_context or {})
        graph = await self.get_graph(context=context)
        async for event in graph.astream(
            {"messages": messages}, stream_mode="values", context=context,
            config=self._input_config(context, **kwargs),
        ):
            yield event["messages"]

    async def check_checkpointer(self) -> bool:
        app = await self.get_graph()
        return bool(getattr(app, "checkpointer", None))

    async def get_history(self, uid, thread_id) -> list[dict]:
        """经 checkpointer 读取历史；无 checkpointer 返回空。"""
        try:
            app = await self.get_graph()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"get_history: build graph failed: {exc}")
            return []
        if not await self.check_checkpointer():
            return []
        config = {"configurable": {"thread_id": thread_id, "uid": uid}}
        try:
            state = await app.aget_state(config)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"aget_state failed: {exc}")
            return []
        result: list[dict] = []
        if state:
            for msg in state.values.get("messages", []):
                if hasattr(msg, "model_dump"):
                    result.append(msg.model_dump())
                elif isinstance(msg, dict):
                    result.append(dict(msg))
                else:
                    result.append({"content": str(msg)})
        return result

    def reload_graph(self) -> None:
        """清空 graph 缓存，下次调用重建。"""
        self.graph = None
        logger.info(f"{self.name} graph 缓存已清空，下次调用时重新构建")

    async def _get_checkpointer(self):
        if self.checkpointer is not None:
            return self.checkpointer
        if self._checkpointer_provider is not None:
            self.checkpointer = self._checkpointer_provider.get_checkpointer()
        return self.checkpointer

    @abstractmethod
    async def get_graph(self, **kwargs) -> CompiledStateGraph:
        """构建并编译对话图实例；编译时挂 checkpointer 以获得历史/恢复。"""