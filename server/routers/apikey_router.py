"""API Key 管理路由（M4）。"""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.models import User, ApiKey
from server.services.operation_log_service import log_operation
from server.utils.auth import generate_api_key

apikey = APIRouter(prefix="/user/apikey", tags=["api-key"])


class ApiKeyCreate(BaseModel):
    name: str


async def _log_key_operation(
    db: AsyncSession,
    user_id: int,
    operation: str,
    details: str,
) -> None:
    await log_operation(db, user_id, operation, details)


@apikey.get("")
async def list_keys(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_required_user),
):
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.uid == user.uid)
        .order_by(ApiKey.created_at.desc())
        .offset(max(skip, 0))
        .limit(limit)
    )
    return {"keys": [k.to_dict() for k in result.scalars().all()]}


@apikey.get("/{key_id}")
async def get_key(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_required_user),
):
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.uid == user.uid))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="Key 不存在")
    return {"key": key.to_dict()}


@apikey.post("")
async def create_key(body: ApiKeyCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    full_key, key_hash, key_prefix = generate_api_key()
    k = ApiKey(uid=user.uid, name=body.name, key_hash=key_hash, key_prefix=key_prefix)
    db.add(k)
    await db.flush()
    await _log_key_operation(db, user.id, "api_key.create", f"key_id={k.id};name={k.name}")
    await db.commit()
    await db.refresh(k)
    return {"key": k.to_dict(), "plain_key": full_key}


@apikey.put("/{key_id}")
async def update_key(key_id: int, body: dict, db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.uid == user.uid))
    k = result.scalar_one_or_none()
    if not k:
        raise HTTPException(status_code=404, detail="Key 不存在")
    if body.get("name"):
        k.name = body["name"]
    await _log_key_operation(db, user.id, "api_key.update", f"key_id={k.id}")
    await db.commit()
    return {"key": k.to_dict()}


@apikey.delete("/{key_id}")
async def delete_key(key_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.uid == user.uid))
    k = result.scalar_one_or_none()
    if not k:
        raise HTTPException(status_code=404, detail="Key 不存在")
    await db.delete(k)
    await _log_key_operation(db, user.id, "api_key.delete", f"key_id={k.id}")
    await db.commit()
    return {"ok": True}


@apikey.post("/{key_id}/rotate")
async def rotate_key(key_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.uid == user.uid))
    k = result.scalar_one_or_none()
    if not k:
        raise HTTPException(status_code=404, detail="Key 不存在")
    full_key, key_hash, key_prefix = generate_api_key()
    k.key_hash = key_hash
    k.key_prefix = key_prefix
    k.revoked_at = None
    await _log_key_operation(db, user.id, "api_key.rotate", f"key_id={k.id}")
    await db.commit()
    await db.refresh(k)
    return {"key": k.to_dict(), "plain_key": full_key}
