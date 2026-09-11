"""认证路由：登录、初始化管理员、/me、用户管理 CRUD。"""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_optional_user, get_required_user
from server.models import User
from server.services.operation_log_service import log_operation
from server.utils.auth import create_access_token, hash_password, verify_password
from server.utils.datetime_utils import utc_now_naive
from server.config import settings

func_count = sa_func.count

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
    user.last_login = utc_now_naive()
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
    await db.flush()
    await log_operation(db, user.id, "user.create", f"username={body.username};role={body.role};domain=default")
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
async def list_users(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """用户列表（管理员）。"""
    _require_admin(current_user)
    result = await db.execute(
        select(User).where(User.is_deleted == 0).order_by(User.id).offset(skip).limit(limit))
    return [u.to_dict() for u in result.scalars().all()]


def _require_admin(user: User) -> None:
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _require_superadmin(user: User) -> None:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="需要超级管理员权限")


@auth.get("/users/page")
async def list_users_page(
    offset: int = 0,
    limit: int = 50,
    search: str = "",
    role: str = "",
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """分页用户列表（管理员）：search 模糊 username/uid，role 过滤。"""
    _require_admin(current_user)
    conds = [User.is_deleted == 0]
    if search:
        conds.append((User.username.ilike(f"%{search}%")) | (User.uid.ilike(f"%{search}%")))
    if role:
        conds.append(User.role == role)
    rows = await db.execute(select(User).where(*conds).order_by(User.id).offset(offset).limit(limit))
    total = await db.execute(select(func_count(User.id)).where(*conds))
    users = [u.to_dict() for u in rows.scalars().all()]
    return {"items": users, "total": total.scalar() or len(users), "limit": limit, "offset": offset}


@auth.get("/users/access-options")
async def get_user_access_options(
    current_user: User = Depends(get_required_user),
):
    """用户管理页的角色/域下拉选项。"""
    _require_admin(current_user)
    return {
        "roles": ["user", "admin", "superadmin"],
        "domains": ["default", "credit", "risk", "marketing"],
    }


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    domain: str = "default"


@auth.post("/users")
async def create_user(
    body: UserCreateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    if body.role == "superadmin" and current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="仅超级管理员可创建超级管理员")
    exists = await db.execute(
        select(User).where(User.username == body.username, User.is_deleted == 0))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(
        username=body.username, uid=body.username,
        password_hash=hash_password(body.password),
        role=body.role, domain=body.domain or "default",
    )
    db.add(user)
    await log_operation(db, current_user.id, "user.create", f"target_user={user.id};username={user.username};role={user.role};domain={user.domain}")
    await db.commit()
    await db.refresh(user)
    return user.to_dict()


@auth.put("/users/{user_id}")
async def update_user(
    user_id: int,
    body: dict,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    r = await db.execute(select(User).where(User.id == user_id, User.is_deleted == 0))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.get("username"):
        user.username = body["username"]
    if body.get("role"):
        if body["role"] == "superadmin" and current_user.role != "superadmin":
            raise HTTPException(status_code=403, detail="仅超级管理员可授予超级管理员")
        user.role = body["role"]
    if body.get("domain"):
        user.domain = body["domain"]
    if body.get("password"):
        user.password_hash = hash_password(body["password"])
    await log_operation(db, current_user.id, "user.update", f"target_user={user.id}")
    await db.commit()
    await db.refresh(user)
    return user.to_dict()


@auth.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    r = await db.execute(select(User).where(User.id == user_id, User.is_deleted == 0))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.is_deleted = 1  # 软删除
    await log_operation(db, current_user.id, "user.delete", f"target_user={user.id}")
    await db.commit()
    return {"ok": True}


@auth.post("/validate-username")
async def validate_username(
    body: dict,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """用户名合法性+唯一性校验。"""
    username = str(body.get("username") or "").strip()
    valid = 2 <= len(username) <= 64 and all(c.isalnum() or c in "_-." for c in username)
    if not valid:
        return {"valid": False, "reason": "长度 2-64，仅限字母数字._-"}
    r = await db.execute(
        select(User).where(User.username == username, User.is_deleted == 0))
    if r.scalar_one_or_none():
        return {"valid": False, "reason": "用户名已存在"}
    return {"valid": True}


@auth.post("/upload-avatar")
async def upload_avatar(
    file: UploadFile,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """上传当前用户头像（本地保存 → 存 URL）。"""
    import os
    import uuid as _uuid

    allowed = (".png", ".jpg", ".jpeg", ".webp", ".gif")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed:
        raise HTTPException(status_code=422, detail=f"仅支持 {'/'.join(allowed)}")
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="头像不能超过 5MB")
    # 头像使用独立目录，由 main.py 以 /uploads/avatars 提供静态访问。
    upload_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads", "avatars"))
    os.makedirs(upload_dir, exist_ok=True)
    name = f"{_uuid.uuid4().hex}{ext}"
    with open(os.path.join(upload_dir, name), "wb") as f:
        f.write(data)
    current_user.avatar = f"/uploads/avatars/{name}"
    await db.commit()
    return {"ok": True, "avatar": current_user.avatar}
