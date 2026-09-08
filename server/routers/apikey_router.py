"""API Key 管理路由（M4）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.models import User, ApiKey
from server.utils.auth import generate_api_key

apikey = APIRouter(prefix="/user/apikey", tags=["api-key"])


class ApiKeyCreate(BaseModel):
    name: str


@apikey.get("")
async def list_keys(db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    result = await db.execute(select(ApiKey).where(ApiKey.uid == user.uid).order_by(ApiKey.created_at.desc()))
    return {"keys": [k.to_dict() for k in result.scalars().all()]}


@apikey.post("")
async def create_key(body: ApiKeyCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    full_key, key_hash, key_prefix = generate_api_key()
    k = ApiKey(uid=user.uid, name=body.name, key_hash=key_hash, key_prefix=key_prefix)
    db.add(k)
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
    await db.commit()
    return {"key": k.to_dict()}


@apikey.delete("/{key_id}")
async def delete_key(key_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.uid == user.uid))
    k = result.scalar_one_or_none()
    if not k:
        raise HTTPException(status_code=404, detail="Key 不存在")
    await db.delete(k)
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
    await db.commit()
    await db.refresh(k)
    return {"key": k.to_dict(), "plain_key": full_key}
