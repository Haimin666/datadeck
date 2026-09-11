"""Ossie 指标注册表：冲突审核与口径维护。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.services.metric_registry import MetricRegistry

metrics = APIRouter(prefix="/metrics", tags=["metrics"])


def _check_admin(user) -> None:
    if getattr(user, "role", "") not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="需要管理员权限")


class MetricUpdate(BaseModel):
    canonical_name: str | None = Field(default=None, min_length=1, max_length=128)
    aliases: list[str] | None = None
    definition: str | None = None
    formula: str | None = None
    unit: str | None = Field(default=None, max_length=32)
    owner: str | None = Field(default=None, max_length=64)
    domain: str | None = Field(default=None, max_length=64)
    ossie_name: str | None = Field(default=None, max_length=160)
    ossie_expression: dict | list | str | None = None
    datatype: str | None = Field(default=None, max_length=32)
    ai_context: dict | None = None
    source_evidence: list | None = None
    status: str | None = Field(default=None, pattern="^(candidate|needs_review|approved|rejected)$")
    conflict_status: str | None = Field(default=None, pattern="^(none|conflict|resolved)$")
    conflict_details: list | None = None


@metrics.get("/registry")
async def list_metrics(
    q: str = Query(default="", max_length=128),
    status: str = Query(default=""),
    conflict_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user=Depends(get_required_user), db: AsyncSession = Depends(get_db),
):
    _check_admin(current_user)
    filters = []
    if status:
        filters.append(MetricRegistry.status == status)
    if conflict_only:
        filters.append(MetricRegistry.conflict_status == "conflict")
    if q:
        needle = f"%{q}%"
        filters.append(or_(MetricRegistry.canonical_name.ilike(needle),
                           MetricRegistry.ossie_name.ilike(needle),
                           MetricRegistry.definition.ilike(needle)))
    result = await db.execute(select(MetricRegistry).where(*filters)
                              .order_by(MetricRegistry.updated_at.desc())
                              .offset(offset).limit(limit))
    return {"items": [item.to_dict() for item in result.scalars().all()]}


@metrics.get("/registry/{metric_id}")
async def get_metric(metric_id: int, current_user=Depends(get_required_user),
                     db: AsyncSession = Depends(get_db)):
    _check_admin(current_user)
    item = await db.get(MetricRegistry, metric_id)
    if item is None:
        raise HTTPException(status_code=404, detail="指标不存在")
    return item.to_dict()


@metrics.put("/registry/{metric_id}")
async def update_metric(metric_id: int, payload: MetricUpdate,
                        current_user=Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    _check_admin(current_user)
    item = await db.get(MetricRegistry, metric_id)
    if item is None:
        raise HTTPException(status_code=404, detail="指标不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item.to_dict()
