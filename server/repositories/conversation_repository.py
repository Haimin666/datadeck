"""Conversation Repository（datadeck 适配层）。

datadeck 的 threads 表对应 Yuxi 的 conversations 表：
- threads.id == conversations.thread_id（主键即线程 UUID）
- threads.uid == conversations.uid
- threads.agent_id == conversations.agent_id（agent slug）
- threads.project_id == conversations.project_id（P2C 新增）
- threads.status/extra_metadata 对齐 Yuxi Conversation（P2C 新增）

本 Repository 把 Thread 对象适配成 workdir/mention/project 服务所期望的
Conversation 形状（thread_id / id 属性）。
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Thread

# Yuxi channel invocation 来源（datadeck 保留语义：这些 source 的对话不进 Project 历史）
INVOCATION_CONVERSATION_SOURCES = ("agent_call", "agent_evaluation")


class ConversationRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def get_conversation_by_thread_id(self, thread_id: str):
        """按 thread_id 读取 Thread 并适配为 Conversation 形状。"""
        result = await self.db.execute(select(Thread).where(Thread.id == thread_id))
        thread = result.scalar_one_or_none()
        if thread is None:
            return None
        return _adapt_thread(thread)


def _adapt_thread(thread: Thread):
    """把 Thread 适配为 workdir/mention 服务使用的 Conversation 视图。

    id 用作 workdir_service 的 conversation_id：datadeck 直接使用 thread_id 字符串。
    """
    return SimpleNamespace(
        id=thread.id,
        thread_id=thread.id,
        uid=thread.uid,
        agent_id=thread.agent_id,
        title=thread.title,
        status=thread.status or "active",
        project_id=thread.project_id or "",
        updated_at=thread.updated_at,
        extra_metadata=thread.extra_metadata,
    )
