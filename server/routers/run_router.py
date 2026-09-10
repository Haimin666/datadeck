"""Run 路由：创建 run（含审批 resume）、获取 run、取消 run、SSE 事件流。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.event_translator import parse_after_seq, poll_run_events
from server.models import Agent, User, AgentRun, Thread
from server.services.run_service import (
    build_resume_command, create_agent_run, dispatch_run, request_cancel,
)

run_router = APIRouter(prefix="/agent/runs", tags=["agent-runs"])


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = None
    agent_slug: str
    thread_id: str
    meta: dict = {}
    image_content: str | None = None
    model_spec: str | None = None
    tool_approval_mode: str | None = None
    resume: str | None = None
    resume_payload: object | None = None
    tool_approval: dict | None = None
    created_by_run_id: str | None = None
    queue_policy: Literal["enqueue", "steer", "direct"] = "enqueue"


@run_router.post("")
async def create_run(
    body: RunCreateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    thread = await db.scalar(
        sa_select(Thread).where(
            Thread.id == body.thread_id,
            Thread.uid == current_user.uid,
            Thread.status == "active",
        )
    )
    if thread is None:
        raise HTTPException(status_code=404, detail="对话不存在")

    target_agent = await db.scalar(
        sa_select(Agent).where(or_(Agent.id == body.agent_slug, Agent.slug == body.agent_slug))
    )
    if target_agent is None or thread.agent_id not in {target_agent.id, target_agent.slug}:
        raise HTTPException(status_code=404, detail="智能体不存在或与对话不匹配")

    if body.tool_approval_mode is not None:
        if body.tool_approval_mode not in {"default", "always_trust", "none"}:
            raise HTTPException(status_code=422, detail="tool_approval_mode 非法")
        thread.tool_approval_mode = body.tool_approval_mode
    if body.model_spec:
        thread.extra_metadata = {**(thread.extra_metadata or {}), "model_spec": body.model_spec}

    resume_command = None
    if body.resume:
        resume_command = (
            build_resume_command(body.tool_approval)
            if body.tool_approval is not None
            else build_resume_command(resume_payload=body.resume_payload)
        )

    active_status = await db.scalar(
        sa_select(AgentRun.status)
        .where(
            AgentRun.thread_id == body.thread_id,
            AgentRun.uid == current_user.uid,
            AgentRun.status.in_(("pending", "running", "cancel_requested", "interrupted")),
            *([AgentRun.id != body.resume] if body.resume else []),
        )
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    if active_status == "interrupted" and not body.resume:
        raise HTTPException(status_code=409, detail="当前对话正在等待用户审批")
    active_count = int(await db.scalar(
        sa_select(func.count(AgentRun.id)).where(
            AgentRun.thread_id == body.thread_id,
            AgentRun.uid == current_user.uid,
            AgentRun.status.in_(("pending", "running", "cancel_requested", "interrupted")),
            *([AgentRun.id != body.resume] if body.resume else []),
        )
    ) or 0)

    try:
        run = await create_agent_run(
            query=body.query or "",
            agent_slug=target_agent.slug,
            thread_id=body.thread_id,
            uid=current_user.uid,
            resume=body.resume,
            model_spec=body.model_spec,
            meta=body.meta,
            image_content=body.image_content,
            queue_policy=body.queue_policy,
            request_id=(body.meta or {}).get("request_id"),
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    queued = bool(active_status) and not body.resume and body.queue_policy != "direct"
    if not queued:
        await dispatch_run(run.id, resume_command=resume_command)
    payload = run.to_dict()
    # 前端 AgentChatComponent 读取顶层 run_id/status/queue_policy（扁平契约），
    # 同时保留 run 键兼容既有调用（如 SSE 契约测试读 json()["run"]）。
    return {
        **payload,
        "run_id": run.id,
        "status": "queued" if queued else run.status,
        "queue_policy": body.queue_policy,
        "queue_position": active_count + 1 if queued else 1,
        "run": payload,
    }


@run_router.get("/{run_id}")
async def get_run(run_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(sa_select(AgentRun).where(AgentRun.id == run_id, AgentRun.uid == current_user.uid))
    run = r.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run 不存在")
    return {"run": run.to_dict()}


@run_router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    try:
        status = await request_cancel(run_id, current_user.uid)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"run_id": run_id, "status": status}


@run_router.get("/{run_id}/events")
async def stream_events(
    run_id: str,
    after_seq: str = Query("0-0"),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    current_user: User = Depends(get_required_user),
):
    cursor = parse_after_seq(last_event_id or after_seq)
    return StreamingResponse(
        poll_run_events(run_id, after_seq=cursor, current_uid=current_user.uid),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
