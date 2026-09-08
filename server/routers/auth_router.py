"""认证路由：登录、初始化管理员、/me。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_optional_user
from server.models import User
from server.utils.auth import create_access_token, hash_password, verify_password
from server.config import settings

auth = APIRouter(prefix="/auth", tags=["authentication"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    uid: str
    role: str


class InitializeRequest(BaseModel):
    username: str
    password: str
    role: str = "superadmin"


@auth.post("/token")
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == form.username, User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    user.last_login = datetime.utcnow()
    await db.commit()
    token = create_access_token(str(user.id))
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        uid=user.uid,
        role=user.role,
    )


@auth.post("/initialize")
async def initialize(body: InitializeRequest, db: AsyncSession = Depends(get_db)):
    """首次运行初始化管理员账户。"""
    result = await db.execute(select(User).where(User.is_deleted == 0).limit(1))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="系统已初始化")
    user = User(
        username=body.username,
        uid=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_access_token(str(user.id))
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        uid=user.uid,
        role=user.role,
    )


@auth.get("/check-first-run")
async def check_first_run(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.is_deleted == 0).limit(1))
    has_user = result.scalar_one_or_none() is not None
    return {"first_run": not has_user}


@auth.get("/me")
async def get_me(user: User = Depends(get_optional_user)):
    if user is None:
        raise HTTPException(status_code=401, detail="未授权")
    return user.to_dict()


@auth.put("/profile")
async def update_profile(
    body: dict,
    user: User = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401, detail="未授权")
    if body.get("username"):
        user.username = body["username"]
    await db.commit()
    return user.to_dict()


@auth.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.is_deleted == 0).order_by(User.id))
    return [u.to_dict() for u in result.scalars().all()]
