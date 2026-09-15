"""聊天路由：调用、线程、消息历史、附件、反馈。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy import or_, select as sa_select, text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user, get_role_permissions, require_agent_access
from server.models import User, Thread, Agent, MessageFeedback, Project
from server.services import attachment_service as att
from server.services.project_service import create_implicit_project
from server.utils.datetime_utils import utc_now_naive
from datadeck import logger


def _display_message_content(content: object) -> str:
    """隐藏旧版本误写入用户消息的内部路由提示。"""
    value = content if isinstance(content, str) else str(content or "")
    if value.startswith("[路由提示]"):
        _, separator, visible = value.partition("\n\n")
        if separator:
            return visible
    return value

chat = APIRouter(prefix="/chat", tags=["chat"])


class ThreadCreateRequest(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=64)
    agent_id: str
    title: str | None = None
    metadata: dict = Field(default_factory=dict)
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
    if body.request_id:
        existing = await db.scalar(
            sa_select(Thread).where(
                Thread.uid == current_user.uid,
                Thread.request_id == body.request_id,
            )
        )
        if existing is not None:
            return existing.to_dict()

    target_agent = await db.scalar(
        sa_select(Agent).where(or_(Agent.id == body.agent_id, Agent.slug == body.agent_id))
    )
    if target_agent is None:
        raise HTTPException(status_code=404, detail="智能体不存在")
    await require_agent_access(db, current_user, target_agent.slug)

    # 项目属于 workspace 模块。没有该模块权限的用户只能创建临时会话，
    # 即使前端或旧客户端提交了 project_id 也不能把它带入线程。
    role_permissions = await get_role_permissions(db, current_user.role)
    project_id = body.project_id if "workspace" in role_permissions else None

    project = None
    if project_id:
        project = await db.scalar(
            sa_select(Project).where(
                Project.id == project_id,
                Project.uid == current_user.uid,
                Project.status == "active",
            )
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project 不存在")
    elif "workspace" in role_permissions:
        project = await create_implicit_project(uid=current_user.uid, db=db)

    thread_id = str(uuid.uuid4())
    t = Thread(
        id=thread_id,
        uid=current_user.uid,
        request_id=body.request_id,
        agent_id=body.agent_id,
        title=body.title or "新的对话",
        project_id=project.id if project else None,
        extra_metadata=body.metadata or {},
    )
    db.add(t)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if not body.request_id:
            raise
        existing = await db.scalar(
            sa_select(Thread).where(
                Thread.uid == current_user.uid,
                Thread.request_id == body.request_id,
            )
        )
        if existing is None:
            raise
        return existing.to_dict()
    if project and project.directory_mode == "managed":
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


@chat.get("/thread/{thread_id}")
async def get_thread(
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """按 ID 获取当前用户线程，避免深链接依赖首屏分页结果。"""
    thread = await db.scalar(sa_select(Thread).where(
        Thread.id == thread_id,
        Thread.uid == current_user.uid,
        Thread.status == "active",
    ))
    if thread is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    return thread.to_dict()


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

    active = await db.execute(sa_text(
        "SELECT 1 FROM agent_runs WHERE thread_id=:tid "
        "AND status IN ('pending', 'running', 'cancel_requested', 'interrupted') LIMIT 1"
    ), {"tid": thread_id})
    if active.fetchone() is not None:
        raise HTTPException(status_code=409, detail="对话仍在运行，请先取消任务后再删除")

    # LangGraph checkpoint 不属于业务 ORM 表，必须通过 checkpointer 的端口
    # 清理；否则删除线程后仍会保留历史消息、interrupt 和运行状态。
    agent = await _get_thread_agent(db, t)
    if agent is not None:
        get_checkpointer = getattr(agent, "_get_checkpointer", None)
        if callable(get_checkpointer):
            try:
                checkpointer = await get_checkpointer()
                delete_thread = getattr(checkpointer, "adelete_thread", None)
                if callable(delete_thread):
                    await delete_thread(thread_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to delete checkpoint for thread %s: %s", thread_id, exc)
                raise HTTPException(status_code=503, detail="会话运行状态清理失败，请稍后重试") from exc

    await db.execute(sa_text(
        "DELETE FROM message_feedback WHERE run_id IN ("
        "SELECT id FROM agent_runs WHERE thread_id=:tid)"
    ), {"tid": thread_id})
    # 部分错误/恢复事件只可靠地记录 run_id，不能只依赖可选的 thread_id。
    await db.execute(sa_text(
        "DELETE FROM run_events WHERE run_id IN ("
        "SELECT id FROM agent_runs WHERE thread_id=:tid)"
    ), {"tid": thread_id})
    await db.execute(sa_text("DELETE FROM thread_attachments WHERE thread_id=:tid"), {"tid": thread_id})
    await db.execute(sa_text("DELETE FROM agent_runs WHERE thread_id=:tid"), {"tid": thread_id})
    await db.delete(t)
    await db.commit()
    try:
        att.remove_thread_storage(thread_id)
    except Exception as exc:  # noqa: BLE001
        # DB 已成功提交，文件清理失败不能回滚业务删除；记录后交给运维清理。
        logger.warning("Failed to remove storage for deleted thread %s: %s", thread_id, exc)
    return {"ok": True}


@chat.get("/thread/{thread_id}/history")
async def get_thread_history(
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """历史消息：读取 checkpointer 真实状态，统一返回 history。

    thread 查不到（含他人 thread）静默返回空，不以 4xx 打断前端。
    """
    r = await db.execute(sa_select(Thread).where(Thread.id == thread_id, Thread.uid == current_user.uid))
    t = r.scalar_one_or_none()
    if not t:
        return {"history": []}

    # SummarizationMiddleware 会在 checkpoint 中用摘要替换旧消息；checkpoint
    # 只服务 Agent 上下文，不能作为用户历史的唯一来源。run_events 保留了每轮
    # 请求和流式回复，因此优先从事件账本恢复完整可见历史。先查事件账本，
    # 避免每次打开历史都装配完整 Agent 和检查点连接。
    event_rows = await db.execute(sa_text(
        "SELECT r.id, r.request_id, r.input_payload, r.created_at, "
        "e.seq, e.event_type, e.payload, e.created_at "
        "FROM agent_runs r LEFT JOIN run_events e ON e.run_id=r.id "
        "WHERE r.thread_id=:tid AND r.uid=:uid "
        "ORDER BY r.created_at ASC, r.id ASC, e.seq ASC"
    ), {"tid": thread_id, "uid": current_user.uid})
    persisted = event_rows.fetchall()
    raw: list[dict] = []
    if persisted:
        rebuilt: list[dict] = []
        current_run = None
        assistant_content: list[str] = []
        assistant_created_at = None

        def flush_assistant() -> None:
            nonlocal assistant_created_at
            if current_run is None or not assistant_content:
                return
            rebuilt.append({
                "id": f"{current_run[0]}-ai",
                "type": "ai",
                "role": "assistant",
                "content": "".join(assistant_content),
                "tool_calls": [],
                "run_id": current_run[0],
                "request_id": current_run[1],
                "created_at": (
                    assistant_created_at.isoformat()
                    if assistant_created_at is not None
                    else current_run[2].isoformat()
                    if current_run[2]
                    else None
                ),
            })
            assistant_content.clear()
            assistant_created_at = None

        for run_id, request_id, input_payload, run_created_at, seq, event_type, payload, event_created_at in persisted:
            if current_run != (run_id, request_id, run_created_at):
                flush_assistant()
                current_run = (run_id, request_id, run_created_at)
                query = (input_payload or {}).get("query", "")
                if query:
                    rebuilt.append({
                        "id": request_id or run_id,
                        "type": "human",
                        "role": "user",
                        "content": _display_message_content(query),
                        "request_id": request_id,
                        "run_id": run_id,
                        "created_at": run_created_at.isoformat() if run_created_at else None,
                    })
            payload = payload or {}
            if event_type == "messages":
                chunk = payload.get("chunk") or {}
                stream_event = chunk.get("stream_event") or {}
                if stream_event.get("type") == "message_delta":
                    if assistant_created_at is None:
                        assistant_created_at = event_created_at
                    assistant_content.append(str(stream_event.get("content") or ""))
            elif event_type == "context_compression":
                flush_assistant()
                rebuilt.append({
                    "id": f"compression-{run_id}-{seq}",
                    "type": "system",
                    "role": "system",
                    "message_type": "context_compression",
                    "content": payload.get("message") or "上下文已压缩，前面的历史消息已保留。",
                    "created_at": event_created_at.isoformat() if event_created_at else None,
                })
        flush_assistant()
        if rebuilt:
            raw = rebuilt
    else:
        # 兼容没有事件账本的旧线程：仅在确实没有持久化事件时读取 checkpoint。
        agent = await _get_thread_agent(db, t)
        if agent is not None:
            raw = await agent.get_history(current_user.uid, thread_id)

    messages = []
    for m in raw:
        msg_type = m.get("type", "")
        if msg_type not in ("human", "ai", "system"):
            continue
        if msg_type == "ai" and not (m.get("content") or "").strip():
            continue
        entry = {
            "id": m.get("id") or str(uuid.uuid4()),
            "type": msg_type,  # 前端 convertServerHistoryToMessages 按 human/ai 分组
            "role": "user" if msg_type == "human" else "system" if msg_type == "system" else "assistant",
            "content": _display_message_content(m.get("content")),
            "tool_calls": m.get("tool_calls") or [],
            "created_at": (
                m.get("created_at")
                if isinstance(m.get("created_at"), str)
                else m.get("created_at").isoformat()
                if m.get("created_at") is not None
                else utc_now_naive().isoformat()
            ),
        }
        messages.append(entry)

    return {"history": messages}


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

    agent = await _get_thread_agent(db, t)
    if agent is None:
        return {"thread_id": thread_id, "agent_state": {}}
    try:
        graph = await agent.get_graph(metadata_only=True)
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


async def _get_thread_agent(db: AsyncSession, thread: Thread):
    """按线程快照选择实际后端，历史和状态读取必须与运行入口一致。"""
    from server.services.agents_provider import get_agent

    target = await db.scalar(
        sa_select(Agent).where(or_(Agent.id == thread.agent_id, Agent.slug == thread.agent_id))
    )
    if target is None:
        return None
    return await get_agent(target.slug, target.backend_id)


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
