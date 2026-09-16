"""认证路由：登录、初始化管理员、/me、用户管理 CRUD。"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import BUILTIN_ROLE_PERMISSIONS, MODULE_PERMISSIONS, get_optional_user, get_required_user, get_role_permissions, normalize_module_permissions
from server.models import Role, User, Agent
from server.services.operation_log_service import log_operation
from server.utils.auth import create_access_token, hash_password, verify_password
from server.utils.datetime_utils import utc_now_naive

func_count = sa_func.count

auth = APIRouter(prefix="/auth", tags=["authentication"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    uid: str
    role: str
    permissions: list[str] = []


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
        permissions=await get_role_permissions(db, user.role),
    )


@auth.get("/goai")
async def login_from_goai(
    user_id: str = Query(..., min_length=1, max_length=64),
    db: AsyncSession = Depends(get_db),
):
    """GoAI 模块入口：按外部 user_id 自动进入普通用户对话。

    该入口不改变本地登录体系。生产环境应仅通过 GoAI 的受信任反向代理暴露，
    因为 user_id 本身不是密码或签名凭证。
    """
    external_uid = user_id.strip()
    user = await db.scalar(
        select(User).where(User.uid == external_uid, User.is_deleted == 0)
    )
    if user is None:
        user = User(
            username=external_uid,
            uid=external_uid,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role="user",
        )
        db.add(user)
        try:
            await db.commit()
            await db.refresh(user)
        except Exception:
            await db.rollback()
            user = await db.scalar(
                select(User).where(User.uid == external_uid, User.is_deleted == 0)
            )
            if user is None:
                raise

    user.last_login = utc_now_naive()
    await db.commit()
    token = create_access_token(str(user.id))
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        uid=user.uid,
        role=user.role,
        permissions=await get_role_permissions(db, user.role),
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
        permissions=await get_role_permissions(db, user.role),
    )


@auth.get("/check-first-run")
async def check_first_run(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.is_deleted == 0).limit(1))
    has_user = result.scalar_one_or_none() is not None
    return {"first_run": not has_user}


@auth.get("/me")
async def get_me(user: User = Depends(get_optional_user), db: AsyncSession = Depends(get_db)):
    if user is None:
        raise HTTPException(status_code=401, detail="未授权")
    return await _serialize_user(db, user)


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
    return await _serialize_user(db, user)


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
    return [await _serialize_user(db, u) for u in result.scalars().all()]


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
    users = [await _serialize_user(db, u) for u in rows.scalars().all()]
    return {"items": users, "total": total.scalar() or len(users), "limit": limit, "offset": offset}


@auth.get("/users/access-options")
async def get_user_access_options(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """用户管理页的角色/域下拉选项。"""
    _require_admin(current_user)
    roles = (await db.execute(select(Role).order_by(Role.created_at))).scalars().all()
    if current_user.role == "superadmin":
        visible_roles = roles
    else:
        own_permissions = set(await get_role_permissions(db, current_user.role))
        visible_roles = [
            role for role in roles
            if role.slug != "superadmin"
            and set(normalize_module_permissions(role.permissions)).issubset(own_permissions)
        ]
    return {
        "roles": [role.to_dict() for role in visible_roles],
        "domains": ["default", "credit", "risk", "marketing"],
    }


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    domain: str = "default"


class UserUpdateRequest(BaseModel):
    username: str | None = Field(default=None, min_length=2, max_length=64)
    password: str | None = Field(default=None, min_length=6, max_length=256)
    role: str | None = Field(default=None, min_length=1, max_length=32)
    domain: str | None = Field(default=None, max_length=64)


class RoleCreateRequest(BaseModel):
    slug: str = Field(min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    permissions: list[str] = Field(default_factory=list)
    agent_slugs: list[str] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] | None = None
    agent_slugs: list[str] | None = None


def _validate_permissions(permissions: list[str]) -> list[str]:
    normalized = normalize_module_permissions(permissions)
    invalid = set(normalized) - MODULE_PERMISSIONS
    if invalid:
        raise HTTPException(status_code=422, detail=f"未知模块权限: {', '.join(sorted(invalid))}")
    return normalized


async def _validate_agent_slugs(db: AsyncSession, slugs: list[str]) -> list[str]:
    normalized = sorted({str(slug).strip() for slug in slugs if str(slug).strip()})
    if not normalized:
        return []
    existing = set((await db.execute(select(Agent.slug).where(Agent.slug.in_(normalized)))).scalars().all())
    missing = set(normalized) - existing
    if missing:
        raise HTTPException(status_code=422, detail=f"未知智能体: {', '.join(sorted(missing))}")
    return normalized


async def _require_assignable_role(db: AsyncSession, role: str, current_user: User) -> None:
    item = await db.get(Role, role)
    if item is None:
        raise HTTPException(status_code=422, detail="指定角色不存在")
    if current_user.role == "superadmin":
        return
    if role == "superadmin":
        raise HTTPException(status_code=403, detail="不能分配超级管理员角色")
    own_permissions = set(await get_role_permissions(db, current_user.role))
    role_permissions = set(normalize_module_permissions(item.permissions))
    if not role_permissions.issubset(own_permissions):
        raise HTTPException(status_code=403, detail="不能分配超出自身权限范围的角色")


async def _serialize_user(db: AsyncSession, user: User) -> dict:
    data = user.to_dict()
    data["permissions"] = await get_role_permissions(db, user.role)
    return data


@auth.get("/roles")
async def list_roles(current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    _require_superadmin(current_user)
    rows = await db.execute(select(Role).order_by(Role.is_builtin.desc(), Role.created_at, Role.slug))
    return {"roles": [item.to_dict() for item in rows.scalars().all()], "modules": sorted(MODULE_PERMISSIONS)}


@auth.post("/roles")
async def create_role(
    payload: RoleCreateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    _require_superadmin(current_user)
    if payload.slug in BUILTIN_ROLE_PERMISSIONS:
        raise HTTPException(status_code=409, detail="不能覆盖内置角色")
    if await db.get(Role, payload.slug):
        raise HTTPException(status_code=409, detail="角色标识已存在")
    if await db.scalar(select(Role.slug).where(Role.name == payload.name.strip())):
        raise HTTPException(status_code=409, detail="角色名称已存在")
    item = Role(
        slug=payload.slug,
        name=payload.name.strip(),
        description=payload.description.strip(),
        permissions=_validate_permissions(payload.permissions),
        agent_slugs=await _validate_agent_slugs(db, payload.agent_slugs),
    )
    db.add(item)
    await log_operation(db, current_user.id, "role.create", f"role={item.slug}")
    await db.commit()
    await db.refresh(item)
    return item.to_dict()


@auth.put("/roles/{role_slug}")
async def update_role(
    role_slug: str,
    payload: RoleUpdateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    _require_superadmin(current_user)
    item = await db.get(Role, role_slug)
    if item is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    if item.is_builtin:
        raise HTTPException(status_code=400, detail="内置角色不可修改")
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates and await db.scalar(
        select(Role.slug).where(Role.name == updates["name"].strip(), Role.slug != role_slug)
    ):
        raise HTTPException(status_code=409, detail="角色名称已存在")
    if "permissions" in updates:
        updates["permissions"] = _validate_permissions(updates["permissions"])
    if "agent_slugs" in updates:
        updates["agent_slugs"] = await _validate_agent_slugs(db, updates["agent_slugs"])
    for key, value in updates.items():
        setattr(item, key, value.strip() if isinstance(value, str) else value)
    await log_operation(db, current_user.id, "role.update", f"role={item.slug}")
    await db.commit()
    await db.refresh(item)
    return item.to_dict()


@auth.delete("/roles/{role_slug}")
async def delete_role(
    role_slug: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    _require_superadmin(current_user)
    item = await db.get(Role, role_slug)
    if item is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    if item.is_builtin:
        raise HTTPException(status_code=400, detail="内置角色不可删除")
    assigned = await db.scalar(select(func_count(User.id)).where(User.role == role_slug, User.is_deleted == 0))
    if assigned:
        raise HTTPException(status_code=409, detail="仍有用户使用该角色，无法删除")
    await db.delete(item)
    await log_operation(db, current_user.id, "role.delete", f"role={role_slug}")
    await db.commit()
    return {"ok": True}


@auth.post("/users")
async def create_user(
    body: UserCreateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    await _require_assignable_role(db, body.role, current_user)
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
    return await _serialize_user(db, user)


@auth.put("/users/{user_id}")
async def update_user(
    user_id: int,
    body: UserUpdateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    r = await db.execute(select(User).where(User.id == user_id, User.is_deleted == 0))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.username:
        user.username = body.username
    if body.role:
        await _require_assignable_role(db, body.role, current_user)
        user.role = body.role
    if body.domain:
        user.domain = body.domain
    if body.password:
        user.password_hash = hash_password(body.password)
    await log_operation(db, current_user.id, "user.update", f"target_user={user.id}")
    await db.commit()
    await db.refresh(user)
    return await _serialize_user(db, user)


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
    upload_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "avatars"))
    os.makedirs(upload_dir, exist_ok=True)
    name = f"{_uuid.uuid4().hex}{ext}"
    with open(os.path.join(upload_dir, name), "wb") as f:
        f.write(data)
    current_user.avatar = f"/uploads/avatars/{name}"
    await db.commit()
    return {"ok": True, "avatar": current_user.avatar}
