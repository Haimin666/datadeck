"""进程内内存 CheckpointerProvider：样例可直接运行（单进程内持久化）。

无参时默认 langgraph 的 InMemorySaver（线程内多轮记忆）；进程退出即丢失。
需要跨进程持久化/审批恢复的宿主应注入 PG 实现（langgraph-checkpoint-postgres）。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from datadeck.ports.checkpointer import CheckpointerProvider


class MemoryCheckpointerProvider(CheckpointerProvider):
    def __init__(self, checkpointer=None) -> None:
        self._checkpointer = checkpointer

    def get_checkpointer(self):
        if self._checkpointer is None:
            self._checkpointer = InMemorySaver()
        return self._checkpointer