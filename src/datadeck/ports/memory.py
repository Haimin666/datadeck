"""MemoryStore: 用户长期记忆适配者接口。

对应 Yuxi 的 ``yuxi.services.memory_service``。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MemoryStore(Protocol):
    """用户级长期记忆的读写。"""

    async def load_prompt(self, uid: str) -> str | None:
        """返回该用户记忆注入文本；无记忆返回 None（Yuxi：不建中间件）。"""

    async def remember(self, *, uid, thread_id, run_id, request_id, worker_id,
                       content: str, replaces: str | None = None) -> dict:
        """用户明确要求时写入长期记忆。"""

    async def search(self, *, uid, query: str, limit: int) -> dict:
        """搜索该用户历史消息。"""

    async def read(self, *, uid, thread_id, message_id=None, limit=20,
                   include_tools=False) -> dict:
        """读取该用户一个线程的有限历史。"""