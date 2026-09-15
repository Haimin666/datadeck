"""Ossie 指标注册表：冲突审核与口径维护。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import require_module_access
from server.models import MetricRegistry

metrics = APIRouter(prefix="/metrics", tags=["metrics"])


def _normalize_metric_context(value: dict | None, status: str) -> dict:
    """让可检索状态与 OSSIE 上下文状态保持同一事实来源。"""
    result = dict(value or {})
    result["review_status"] = status
    return result


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


class MetricCreate(BaseModel):
    canonical_name: str = Field(..., min_length=1, max_length=128)
    aliases: list[str] = Field(default_factory=list)
    definition: str = Field(..., min_length=1)
    formula: str | None = None
    unit: str | None = Field(default=None, max_length=32)
    owner: str | None = Field(default=None, max_length=64)
    domain: str | None = Field(default=None, max_length=64)
    ossie_name: str | None = Field(default=None, max_length=160)
    ossie_expression: dict | list | str | None = None
    datatype: str | None = Field(default=None, max_length=32)
    ai_context: dict = Field(default_factory=dict)
    source_evidence: list = Field(default_factory=list)
    status: str = Field(default="candidate", pattern="^(candidate|needs_review|approved|rejected)$")
    conflict_status: str = Field(default="none", pattern="^(none|conflict|resolved)$")
    conflict_details: list = Field(default_factory=list)


@metrics.get("/registry")
async def list_metrics(
    q: str = Query(default="", max_length=128),
    status: str = Query(default=""),
    conflict_only: bool = False,
    limit: int = Query(default=10000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    current_user=Depends(require_module_access("metrics")), db: AsyncSession = Depends(get_db),
):
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
    base_query = select(MetricRegistry).where(*filters)
    total = await db.scalar(select(func.count(MetricRegistry.id)).where(*filters))
    result = await db.execute(base_query.order_by(MetricRegistry.updated_at.desc())
                              .offset(offset).limit(limit))
    return {"items": [item.to_dict() for item in result.scalars().all()],
            "total": int(total or 0), "limit": limit, "offset": offset,
            "has_more": offset + min(int(total or 0), limit) < int(total or 0)}


@metrics.post("/registry")
async def create_metric(payload: MetricCreate,
                        current_user=Depends(require_module_access("metrics")),
                        db: AsyncSession = Depends(get_db)):
    existing = await db.scalar(select(MetricRegistry).where(
        MetricRegistry.canonical_name == payload.canonical_name.strip()))
    if existing is not None:
        raise HTTPException(status_code=409, detail="指标名称已存在")
    data = payload.model_dump()
    data["ai_context"] = _normalize_metric_context(data.get("ai_context"), data["status"])
    item = MetricRegistry(**data)
    item.canonical_name = item.canonical_name.strip()
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item.to_dict()


@metrics.get("/registry/{metric_id}")
async def get_metric(metric_id: int, current_user=Depends(require_module_access("metrics")),
                     db: AsyncSession = Depends(get_db)):
    item = await db.get(MetricRegistry, metric_id)
    if item is None:
        raise HTTPException(status_code=404, detail="指标不存在")
    return item.to_dict()


@metrics.put("/registry/{metric_id}")
async def update_metric(metric_id: int, payload: MetricUpdate,
                        current_user=Depends(require_module_access("metrics")), db: AsyncSession = Depends(get_db)):
    item = await db.get(MetricRegistry, metric_id)
    if item is None:
        raise HTTPException(status_code=404, detail="指标不存在")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(item, key, value)
    # status 是审核的唯一事实字段；即使前端只提交了 ai_context，也不能留下
    # “status=approved、review_status=needs_review”这类会误导 Agent 的组合。
    item.ai_context = _normalize_metric_context(item.ai_context, item.status)
    await db.commit()
    await db.refresh(item)
    return item.to_dict()


@metrics.delete("/registry/{metric_id}")
async def delete_metric(metric_id: int, current_user=Depends(require_module_access("metrics")),
                        db: AsyncSession = Depends(get_db)):
    """删除一条指标注册记录；不会删除原始 Wiki、代码或 RAG 物料。"""
    item = await db.get(MetricRegistry, metric_id)
    if item is None:
        raise HTTPException(status_code=404, detail="指标不存在")
    await db.delete(item)
    await db.commit()
    return {"ok": True, "id": metric_id}
