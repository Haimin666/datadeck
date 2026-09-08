"""Dashboard 路由（M6）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.models import User, Thread, AgentRun, MessageFeedback

dashboard = APIRouter(prefix="/dashboard", tags=["dashboard"])


@dashboard.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    if user.role not in ("admin", "superadmin"):
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    r_users = await db.execute(select(func.count()).where(User.is_deleted == 0))
    r_threads = await db.execute(select(func.count()).select_from(Thread))
    r_runs = await db.execute(select(func.count()).select_from(AgentRun))
    r_feedbacks = await db.execute(select(func.count()).select_from(MessageFeedback))

    return {
        "total_users": r_users.scalar_one(),
        "total_threads": r_threads.scalar_one(),
        "total_runs": r_runs.scalar_one(),
        "total_feedbacks": r_feedbacks.scalar_one(),
    }


@dashboard.get("/users")
async def list_users(db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    if user.role not in ("admin", "superadmin"):
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    r = await db.execute(select(User).where(User.is_deleted == 0).order_by(User.id))
    return {"users": [u.to_dict() for u in r.scalars().all()]}


@dashboard.get("/calls/timeseries")
async def calls_timeseries(db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    if user.role not in ("admin", "superadmin"):
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    # 简化版：按天聚合
    return {"data": []}
