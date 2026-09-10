"""定时任务 CRUD API；调度执行器后续复用此持久化定义。"""
from __future__ import annotations

import uuid
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_admin_user
from server.models import Agent, Project, ScheduledTask, User

scheduled_tasks = APIRouter(prefix="/scheduled-tasks", tags=["scheduled-tasks"])


class ScheduledTaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    cron: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=10000)
    agent_slug: str = Field(default="default-chatbot", max_length=128)
    project_id: str | None = None
    enabled: bool = True


def _validate_cron(value: str) -> str:
    fields = value.split()
    if len(fields) != 5 or any(not re.fullmatch(r"[0-9*/,\-]+", item) for item in fields):
        raise ValueError("Cron 必须是 5 段数字表达式，例如：0 9 * * *")
    return value


async def _get(db: AsyncSession, uid: str, task_id: str) -> ScheduledTask:
    item = (await db.execute(select(ScheduledTask).where(
        ScheduledTask.id == task_id, ScheduledTask.uid == uid
    ))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return item


async def _require_agent(db: AsyncSession, agent_slug: str) -> None:
    item = (await db.execute(select(Agent.id).where(Agent.slug == agent_slug))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=422, detail="指定的智能体不存在")


async def _require_project(db: AsyncSession, uid: str, project_id: str | None) -> None:
    if project_id and (await db.scalar(select(Project.id).where(
        Project.id == project_id, Project.uid == uid, Project.status == "active"
    ))) is None:
        raise HTTPException(status_code=422, detail="指定的项目不存在")


@scheduled_tasks.get("")
async def list_scheduled_tasks(current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    items = (await db.execute(select(ScheduledTask).where(ScheduledTask.uid == current_user.uid).order_by(ScheduledTask.updated_at.desc()))).scalars().all()
    return {"tasks": [item.to_dict() for item in items]}


@scheduled_tasks.post("")
async def create_scheduled_task(payload: ScheduledTaskIn, current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    try:
        payload.cron = _validate_cron(payload.cron.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _require_agent(db, payload.agent_slug)
    await _require_project(db, current_user.uid, payload.project_id)
    item = ScheduledTask(id=str(uuid.uuid4()), uid=current_user.uid, **payload.model_dump())
    db.add(item)
    await db.flush()
    return item.to_dict()


@scheduled_tasks.put("/{task_id}")
async def update_scheduled_task(task_id: str, payload: ScheduledTaskIn, current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    try:
        payload.cron = _validate_cron(payload.cron.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = await _get(db, current_user.uid, task_id)
    await _require_agent(db, payload.agent_slug)
    await _require_project(db, current_user.uid, payload.project_id)
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    await db.flush()
    return item.to_dict()


@scheduled_tasks.delete("/{task_id}")
async def delete_scheduled_task(task_id: str, current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    item = await _get(db, current_user.uid, task_id)
    await db.delete(item)
    return {"ok": True}
