"""PG MemoryStore（阶段四 4.3）：长期记忆持久化，替代内存 stub。

表 agent_memories：uid + content + replaces（幂等覆盖）+ 时间。
- load_prompt: 拼接该 uid 全部记忆为注入文本（最近优先，带序号）
- remember: upsert（replaces 命中则覆盖原文）
- search: ILIKE 关键词（记忆条数少，够用；后续可换 pg_trgm/向量）
- read: 按时间倒序取 limit 条
"""

from __future__ import annotations

from sqlalchemy import text as sa_text

from server.db import async_session_factory
from server.utils.datetime_utils import utc_now_naive


class PgMemoryStore:
    """实现 datadeck.ports.memory.MemoryStore。"""

    async def load_prompt(self, uid: str, project_id: str | None = None) -> str | None:
        async with async_session_factory() as db:
            r = await db.execute(sa_text(
                "SELECT content FROM agent_memories WHERE uid=:uid "
                "AND (project_id IS NULL OR project_id=:pid) "
                "ORDER BY created_at DESC LIMIT 20"), {"uid": uid, "pid": project_id})
            rows = [x[0] for x in r.fetchall()]
        if not rows:
            return None
        body = "\n".join(f"- {str(c)[:2000]}" for c in rows)
        return f"<| 用户长期记忆（按需参考，勿逐字复述） |>\n{body}"

    async def remember(self, *, uid, thread_id=None, run_id=None, request_id=None,
                       worker_id=None, project_id: str | None = None, content: str,
                       replaces: str | None = None) -> dict:
        async with async_session_factory() as db:
            if replaces:
                result = await db.execute(sa_text(
                    "UPDATE agent_memories SET content=:c, created_at=:now "
                    "WHERE uid=:uid AND (project_id IS NULL OR project_id=:pid) "
                    "AND CAST(id AS TEXT)=:rid"),
                    {"c": content, "now": utc_now_naive(), "uid": uid, "pid": project_id, "rid": replaces})
                if result.rowcount:
                    await db.commit()
                    return {"ok": True, "updated": True}
            await db.execute(sa_text(
                "INSERT INTO agent_memories (uid, project_id, thread_id, run_id, content, replaces, created_at) "
                "VALUES (:uid, :pid, :tid, :rid, :c, :rep, :now)"),
                {"uid": uid, "pid": project_id, "tid": thread_id, "rid": run_id, "c": content,
                 "rep": replaces, "now": utc_now_naive()})
            await db.commit()
        return {"ok": True}

    async def search(self, *, uid, query: str, limit: int = 10,
                     project_id: str | None = None) -> dict:
        limit = min(max(int(limit or 10), 1), 20)
        async with async_session_factory() as db:
            r = await db.execute(sa_text(
                "SELECT content, created_at FROM agent_memories "
                "WHERE uid=:uid AND (project_id IS NULL OR project_id=:pid) "
                "AND content ILIKE :q ORDER BY created_at DESC LIMIT :n"),
                {"uid": uid, "pid": project_id, "q": f"%{query}%", "n": limit})
            rows = r.fetchall()
        return {"results": [
            {"content": c, "created_at": t.isoformat() if t else None} for c, t in rows
        ]}

    async def read(self, *, uid, thread_id=None, message_id=None, limit=20,
                   include_tools=False, project_id: str | None = None) -> dict:
        limit = min(max(int(limit or 20), 1), 20)
        async with async_session_factory() as db:
            if thread_id:
                r = await db.execute(sa_text(
                    "SELECT content, created_at FROM agent_memories "
                    "WHERE uid=:uid AND (project_id IS NULL OR project_id=:pid) "
                    "AND thread_id=:tid ORDER BY created_at DESC LIMIT :n"),
                    {"uid": uid, "pid": project_id, "tid": thread_id, "n": limit})
            else:
                r = await db.execute(sa_text(
                    "SELECT content, created_at FROM agent_memories "
                    "WHERE uid=:uid AND (project_id IS NULL OR project_id=:pid) "
                    "ORDER BY created_at DESC LIMIT :n"),
                    {"uid": uid, "pid": project_id, "n": limit})
            rows = r.fetchall()
        return {"messages": [
            {"content": c, "created_at": t.isoformat() if t else None} for c, t in rows
        ]}
