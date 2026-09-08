"""Checkpointer: LangGraph 状态检查点适配者接口。

对应 Yuxi 的 ``yuxi.storage.postgres.manager.pg_manager.get_langgraph_checkpointer()``。
宿主可以提供 PG 实现（langgraph-checkpoint-postgres）或内存实现（样例）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CheckpointerProvider(Protocol):
    """提供一个 LangGraph checkpointer 实例（用于历史/恢复/审批断点）。"""

    def get_checkpointer(self):
        ...