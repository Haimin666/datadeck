"""FastAPI 依赖注入。"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.models import User, ApiKey
from server.utils.auth import decode_access_token, derive_api_key_hash
from server.config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


async def get_required_user(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """支持 Bearer JWT 或 X-API-Key 双通道认证。"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未授权",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user: User | None = None

    # 1. Bearer JWT
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split("Bearer ", 1)[1].strip()
        if token:
            payload = decode_access_token(token)
            if payload:
                user_id = payload.get("sub")
                if user_id:
                    result = await db.execute(select(User).where(User.id == int(user_id), User.is_deleted == 0))
                    user = result.scalar_one_or_none()

    # 2. X-API-Key
    if user is None and x_api_key:
        key_hash = derive_api_key_hash(x_api_key)
        result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None)))
        api_key = result.scalar_one_or_none()
        if api_key:
            result = await db.execute(select(User).where(User.id == api_key.uid, User.is_deleted == 0))
            user = result.scalar_one_or_none()
            if user:
                api_key.last_used_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)

    if user is None:
        raise credentials_exception
    return user


async def get_optional_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split("Bearer ", 1)[1].strip()
    payload = decode_access_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == int(user_id), User.is_deleted == 0))
    return result.scalar_one_or_none()
