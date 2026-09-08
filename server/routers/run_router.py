"""Run 路由：创建 run（含审批 resume）、获取 run、取消 run、SSE 事件流。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.event_translator import parse_after_seq, poll_run_events
from server.models import User, AgentRun
from server.services.run_service import (
    build_resume_command, create_agent_run, dispatch_run, request_cancel,
)

run_router = APIRouter(prefix="/agent/runs", tags=["agent-runs"])


class RunCreateRequest(BaseModel):
    query: str | None = None
    agent_slug: str
    thread_id: str
    meta: dict = {}
    image_content: str | None = None
    model_spec: str | None = None
    tool_approval_mode: str | None = None
    resume: str | None = None
    tool_approval: dict | None = None
    queue_policy: str = "enqueue"


@run_router.post("")
async def create_run(
    body: RunCreateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    resume_command = None
    if body.resume:
        resume_command = build_resume_command(body.tool_approval)

    try:
        run = await create_agent_run(
            query=body.query or "",
            agent_slug=body.agent_slug,
            thread_id=body.thread_id,
            uid=current_user.uid,
            resume=body.resume,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await dispatch_run(run.id, resume_command=resume_command)
    return {"run": run.to_dict()}


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
