"""PG MemoryStore（阶段四 4.3）：长期记忆持久化，替代内存 stub。

表 agent_memories：uid + content + replaces（幂等覆盖）+ 时间。
- load_prompt: 拼接该 uid 全部记忆为注入文本（最近优先，带序号）
- remember: upsert（replaces 命中则覆盖原文）
- search: ILIKE 关键词（记忆条数少，够用；后续可换 pg_trgm/向量）
- read: 按时间倒序取 limit 条
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, text as sa_text

from server.db import async_session_factory
from server.models import Base
from server.utils.datetime_utils import utc_now_naive


class AgentMemory(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (Index("ix_agent_memories_uid", "uid"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(String(64), nullable=False)
    thread_id = Column(String(64), nullable=True)
    run_id = Column(String(64), nullable=True)
    content = Column(Text, nullable=False)
    replaces = Column(String(128), nullable=True)  # 替换目标记忆 id（幂等覆盖）
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)


class PgMemoryStore:
    """实现 datadeck.ports.memory.MemoryStore。"""

    async def load_prompt(self, uid: str) -> str | None:
        async with async_session_factory() as db:
            r = await db.execute(sa_text(
                "SELECT content FROM agent_memories WHERE uid=:uid "
                "ORDER BY created_at DESC LIMIT 20"), {"uid": uid})
            rows = [x[0] for x in r.fetchall()]
        if not rows:
            return None
        body = "\n".join(f"- {c}" for c in rows)
        return f"<| 用户长期记忆（按需参考，勿逐字复述） |>\n{body}"

    async def remember(self, *, uid, thread_id=None, run_id=None, request_id=None,
                       worker_id=None, content: str, replaces: str | None = None) -> dict:
        async with async_session_factory() as db:
            if replaces:
                await db.execute(sa_text(
                    "UPDATE agent_memories SET content=:c, created_at=:now "
                    "WHERE uid=:uid AND CAST(id AS TEXT)=:rid"),
                    {"c": content, "now": utc_now_naive(), "uid": uid, "rid": replaces})
            await db.execute(sa_text(
                "INSERT INTO agent_memories (uid, thread_id, run_id, content, replaces, created_at) "
                "VALUES (:uid, :tid, :rid, :c, :rep, :now)"),
                {"uid": uid, "tid": thread_id, "rid": run_id, "c": content,
                 "rep": replaces, "now": utc_now_naive()})
            await db.commit()
        return {"ok": True}

    async def search(self, *, uid, query: str, limit: int = 10) -> dict:
        async with async_session_factory() as db:
            r = await db.execute(sa_text(
                "SELECT content, created_at FROM agent_memories "
                "WHERE uid=:uid AND content ILIKE :q ORDER BY created_at DESC LIMIT :n"),
                {"uid": uid, "q": f"%{query}%", "n": limit})
            rows = r.fetchall()
        return {"results": [
            {"content": c, "created_at": t.isoformat() if t else None} for c, t in rows
        ]}

    async def read(self, *, uid, thread_id=None, message_id=None, limit=20,
                   include_tools=False) -> dict:
        async with async_session_factory() as db:
            if thread_id:
                r = await db.execute(sa_text(
                    "SELECT content, created_at FROM agent_memories "
                    "WHERE uid=:uid AND thread_id=:tid ORDER BY created_at DESC LIMIT :n"),
                    {"uid": uid, "tid": thread_id, "n": limit})
            else:
                r = await db.execute(sa_text(
                    "SELECT content, created_at FROM agent_memories "
                    "WHERE uid=:uid ORDER BY created_at DESC LIMIT :n"),
                    {"uid": uid, "n": limit})
            rows = r.fetchall()
        return {"messages": [
            {"content": c, "created_at": t.isoformat() if t else None} for c, t in rows
        ]}
