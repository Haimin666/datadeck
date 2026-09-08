"""进程内内存 MemoryStore：样例用，无持久化。

生产宿主应将 Yuxi 的 ``yuxi.services.memory_service`` 移植为 MemoryStore 实现。
"""

from __future__ import annotations

from datadeck.ports.memory import MemoryStore


class MemoryMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._prompts: dict[str, str] = {}

    async def load_prompt(self, uid: str) -> str | None:
        return self._prompts.get(uid)

    async def remember(self, *, uid, thread_id, run_id, request_id, worker_id,
                       content: str, replaces: str | None = None) -> dict:
        if replaces and self._prompts.get(uid) == replaces:
            self._prompts[uid] = content
        else:
            self._prompts[uid] = (self._prompts.get(uid, "") + "\n" + content).strip()
        return {"status": "ok"}

    async def search(self, *, uid, query: str, limit: int) -> dict:
        return {"status": "ok", "results": []}

    async def read(self, *, uid, thread_id, message_id=None, limit=20,
                   include_tools=False) -> dict:
        return {"status": "ok", "messages": []}