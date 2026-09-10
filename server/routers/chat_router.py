"""聊天路由：调用、线程、消息历史、附件、反馈。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy import select as sa_select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.models import User, Thread, AgentRun, Agent, RunEvent, MessageFeedback, Project
from server.services.project_service import create_implicit_project
from server.utils.datetime_utils import utc_now_naive
from datadeck import logger

chat = APIRouter(prefix="/chat", tags=["chat"])


class SimpleCallRequest(BaseModel):
    query: str
    meta: dict = {}


@chat.post("/call")
async def simple_call(body: SimpleCallRequest, current_user: User = Depends(get_required_user)):
    """非流式简单调用，用于标题生成等场景（无线程上下文，一次性调用）。"""
    from datadeck.agents.buildin.chatbot.graph import ChatbotAgent
    from datadeck.adapters.platform_model_provider import PlatformModelProvider
    from datadeck.adapters.checkpointer import MemoryCheckpointerProvider

    provider = PlatformModelProvider()
    agent = ChatbotAgent(
        model_provider=provider,
        checkpointer_provider=MemoryCheckpointerProvider(),
    )
    result = await agent.invoke_messages([{"role": "user", "content": body.query}])
    messages = result.get("messages", [])
    last_msg = messages[-1] if messages else {"content": ""}
    content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
    return {
        "response": content,
        "request_id": body.meta.get("request_id", str(uuid.uuid4())),
    }


class ThreadCreateRequest(BaseModel):
    agent_id: str
    title: str | None = None
    metadata: dict = {}
    project_id: str | None = None


class ThreadUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    is_pinned: bool | None = None
    tool_approval_mode: Literal["default", "always_trust", "none"] | None = None
    metadata: dict | None = None


@chat.post("/thread")
async def create_thread(
    body: ThreadCreateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    project = None
    if body.project_id:
        project = await db.scalar(
            sa_select(Project).where(
                Project.id == body.project_id,
                Project.uid == current_user.uid,
                Project.status == "active",
            )
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project 不存在")
    else:
        project = await create_implicit_project(uid=current_user.uid, db=db)

    thread_id = str(uuid.uuid4())
    t = Thread(
        id=thread_id,
        uid=current_user.uid,
        agent_id=body.agent_id,
        title=body.title or "新的对话",
        project_id=project.id,
        extra_metadata=body.metadata or {},
    )
    db.add(t)
    await db.commit()
    if project.directory_mode == "managed":
        # 主动物化会话 Workdir（供后续 workspace/project 功能）。目录不可写/暂不可用
        # 不应阻断会话创建——真正使用该 Workdir 的路径会再次尝试并显式报错。
        try:
            from server.workspace.paths import ensure_bound_user_workdir

            ensure_bound_user_workdir(current_user.uid, project.workdir_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"create_thread: 会话 Workdir 物化失败（不影响对话）: {exc}")
    await db.refresh(t)
    return t.to_dict()


@chat.get("/threads")
async def list_threads(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    agent_id: str = Query("", description="按 agent 过滤（空=全部）"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    conds = [Thread.uid == current_user.uid, Thread.status == "active"]
    if agent_id:
        conds.append(Thread.agent_id == agent_id)
    r = await db.execute(
        sa_select(Thread)
        .where(*conds)
        .order_by(Thread.is_pinned.desc(), Thread.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    threads = r.scalars().all()
    return [t.to_dict() for t in threads]


@chat.get("/threads/search")
async def search_threads(
    q: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    pattern = f"%{q}%"
    r = await db.execute(
        sa_select(Thread)
        .where(Thread.uid == current_user.uid, Thread.status == "active", Thread.title.ilike(pattern))
        .order_by(Thread.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    threads = r.scalars().all()
    return {
        "items": [t.to_dict() for t in threads],
        "has_more": len(threads) == limit,
        "limit": limit,
        "offset": offset,
    }


@chat.put("/thread/{thread_id}")
async def update_thread(
    thread_id: str,
    body: ThreadUpdateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(sa_select(Thread).where(Thread.id == thread_id, Thread.uid == current_user.uid))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="对话不存在")
    if body.title is not None:
        t.title = body.title
    if "is_pinned" in body.model_fields_set and body.is_pinned is not None:
        t.is_pinned = body.is_pinned
    if body.tool_approval_mode is not None:
        t.tool_approval_mode = body.tool_approval_mode
    if body.metadata is not None:
        t.extra_metadata = body.metadata
    await db.commit()
    await db.refresh(t)
    return t.to_dict()


@chat.post("/thread/{thread_id}/viewed")
async def mark_thread_viewed(
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(sa_select(Thread).where(Thread.id == thread_id, Thread.uid == current_user.uid))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="对话不存在")
    t.viewed_at = utc_now_naive()
    await db.commit()
    return t.to_dict()


@chat.delete("/thread/{thread_id}")
async def delete_thread(
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(sa_select(Thread).where(Thread.id == thread_id, Thread.uid == current_user.uid))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="对话不存在")
    # 兼容旧数据库：message_feedbacks 可能尚未完成迁移，不能因此阻断线程删除。
    feedback_table = await db.execute(sa_text(
        "SELECT to_regclass('public.message_feedbacks')"
    ))
    if feedback_table.scalar_one_or_none() is not None:
        await db.execute(sa_text(
            "DELETE FROM message_feedbacks WHERE run_id IN ("
            "SELECT id FROM agent_runs WHERE thread_id=:tid)"
        ), {"tid": thread_id})
    await db.execute(sa_text("DELETE FROM run_events WHERE thread_id=:tid"), {"tid": thread_id})
    await db.execute(sa_text("DELETE FROM agent_runs WHERE thread_id=:tid"), {"tid": thread_id})
    await db.delete(t)
    await db.commit()
    return {"ok": True}


@chat.get("/thread/{thread_id}/history")
async def get_thread_history(
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """历史消息：读 checkpointer 真实状态（DEVELOPMENT.md M1：history 零自研）。

    契约对齐 1:1 迁移的 Yuxi 前端（fetchThreadMessages 读 response.history，
    条目按 type='human'|'ai' 分组渲染）。同时保留 messages/last_seq 兼容旧调用。

    thread 查不到（含他人 thread）静默返回空——不 4xx 打断前端（§2.6 坑③）。
    """
    r = await db.execute(sa_select(Thread).where(Thread.id == thread_id, Thread.uid == current_user.uid))
    t = r.scalar_one_or_none()
    if not t:
        return {"history": [], "messages": [], "last_seq": "0-0"}

    from server.services.agents_provider import get_chatbot_agent
    agent = await get_chatbot_agent()
    raw = await agent.get_history(current_user.uid, thread_id)

    messages = []
    last_seq = "0-0"
    for m in raw:
        msg_type = m.get("type", "")
        if msg_type not in ("human", "ai"):
            continue
        if msg_type == "ai" and not (m.get("content") or "").strip():
            continue
        entry = {
            "id": m.get("id") or str(uuid.uuid4()),
            "type": msg_type,  # 前端 convertServerHistoryToMessages 按 human/ai 分组
            "role": "user" if msg_type == "human" else "assistant",
            "content": m.get("content") or "",
            "tool_calls": m.get("tool_calls") or [],
            "created_at": utc_now_naive().isoformat(),
        }
        messages.append(entry)

    # last_seq 用该 thread 最新 run_event（前端仅作断线游标，不再回放历史）
    r = await db.execute(
        sa_text("SELECT COALESCE(MAX(seq), 0) FROM run_events WHERE thread_id=:tid"),
        {"tid": thread_id},
    )
    seq = r.scalar_one()
    if seq:
        last_seq = str(seq)

    return {"history": messages, "messages": messages, "last_seq": last_seq}


@chat.get("/thread/{thread_id}/state")
async def get_thread_state(
    thread_id: str,
    include_messages: bool = Query(False),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """AgentState：优先 checkpointer 真实 state（todos/sql_validation/token_usage）。"""
    r = await db.execute(sa_select(Thread).where(Thread.id == thread_id, Thread.uid == current_user.uid))
    t = r.scalar_one_or_none()
    if not t:
        return {"thread_id": thread_id, "agent_state": {}}

    from server.services.agents_provider import get_chatbot_agent
    agent = await get_chatbot_agent()
    try:
        graph = await agent.get_graph()
        snap = await graph.aget_state({"configurable": {"thread_id": thread_id, "uid": current_user.uid}})
        values = dict(snap.values or {})
    except Exception:  # noqa: BLE001
        values = {}

    agent_state = {
        k: values.get(k)
        for k in ("todos", "artifacts", "sql_validation")
        if values.get(k) is not None
    }
    token_usage = values.get("token_usage")
    if token_usage:
        agent_state["token_usage"] = token_usage

    return {"thread_id": thread_id, "agent_state": agent_state, "token_usage": token_usage}


async def _get_active_run(thread_id: str, uid: str, db: AsyncSession):
    """线程当前仍需前端关注的最近一个 run（含 interrupted，供中断恢复）。

    契约对齐 Yuxi get_active_run_by_thread：pending/running/cancel_requested/interrupted
    视为 active，其余（终态）返回 run=None。
    """
    r = await db.execute(
        sa_text(
            "SELECT id, status, agent_slug, created_at FROM agent_runs "
            "WHERE thread_id=:tid AND uid=:uid "
            "AND status IN ('pending','running','cancel_requested','interrupted') "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"tid": thread_id, "uid": uid},
    )
    row = r.fetchone()
    if row:
        return {"run": {"id": row[0], "status": row[1], "agent_slug": row[2], "created_at": str(row[3])}}
    return {"run": None}


@chat.get("/thread/{thread_id}/active-run")
async def get_active_run(
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_active_run(thread_id, current_user.uid, db)


@chat.get("/thread/{thread_id}/active_run")
async def get_active_run_alias(
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """前端 active_run 命名兼容别名。"""
    return await _get_active_run(thread_id, current_user.uid, db)


class FeedbackRequest(BaseModel):
    rating: str
    reason: str | None = None


@chat.post("/message/{message_id}/feedback")
async def submit_feedback(
    message_id: str,
    body: FeedbackRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    fb = MessageFeedback(
        run_id=message_id,
        message_id=message_id,
        rating=body.rating,
        reason=body.reason,
        uid=current_user.uid,
    )
    db.add(fb)
    await db.commit()
    return {"ok": True}


@chat.get("/message/{message_id}/feedback")
async def get_feedback(
    message_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(
        sa_select(MessageFeedback).where(
            MessageFeedback.message_id == message_id,
            MessageFeedback.uid == current_user.uid,
        )
    )
    fb = r.scalar_one_or_none()
    if fb:
        return fb.to_dict()
    return {"rating": None}
