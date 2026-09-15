"""FastAPI 依赖注入。"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.models import User, ApiKey, Role
from server.utils.auth import decode_access_token, derive_api_key_hash
from server.utils.datetime_utils import utc_now_naive

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)

MODULE_PERMISSIONS = frozenset({
    "conversations", "agents", "workspace", "knowledge", "extensions", "scheduled_tasks", "metrics",
    "settings", "users",
})
BUILTIN_ROLE_PERMISSIONS = {
    "superadmin": sorted(MODULE_PERMISSIONS),
    "admin": ["conversations", "agents", "workspace", "knowledge", "extensions", "scheduled_tasks", "metrics", "settings", "users"],
    "user": ["conversations", "workspace"],
}

def normalize_module_permissions(permissions: list[str] | None) -> list[str]:
    return sorted({str(item).strip() for item in permissions or [] if str(item).strip()})


async def get_role_permissions(db: AsyncSession, role_slug: str) -> list[str]:
    """Resolve current permissions from the database with built-in role defaults."""
    if role_slug == "superadmin":
        return BUILTIN_ROLE_PERMISSIONS["superadmin"]
    role = await db.get(Role, role_slug)
    if role is not None:
        return [item for item in normalize_module_permissions(role.permissions) if item in MODULE_PERMISSIONS]
    return BUILTIN_ROLE_PERMISSIONS.get(role_slug, BUILTIN_ROLE_PERMISSIONS["user"])


async def get_role_agent_slugs(db: AsyncSession, role_slug: str) -> set[str] | None:
    """Return the role's Agent allow-list.

    Built-in roles keep their platform-wide Agent access. A custom role is
    explicit: an empty assignment means it currently has no Agent access.
    """
    if role_slug == "superadmin":
        return None
    role = await db.get(Role, role_slug)
    if role is None:
        return None
    if role.is_builtin:
        return None
    return {str(slug).strip() for slug in role.agent_slugs if str(slug).strip()}


async def require_agent_access(db: AsyncSession, user: User, agent_slug: str) -> None:
    """Enforce role-level Agent assignment for runtime calls."""
    allowed = await get_role_agent_slugs(db, user.role)
    if allowed is not None and agent_slug not in allowed:
        raise HTTPException(status_code=403, detail="当前角色未分配该智能体")


def require_module_access(module: str):
    if module not in MODULE_PERMISSIONS:
        raise ValueError(f"Unknown module permission: {module}")

    async def dependency(
        user: User = Depends(get_required_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if module not in await get_role_permissions(db, user.role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="没有访问该模块的权限")
        return user

    return dependency


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
                    try:
                        numeric_user_id = int(user_id)
                    except (TypeError, ValueError):
                        numeric_user_id = None
                    if numeric_user_id is not None:
                        result = await db.execute(
                            select(User).where(User.id == numeric_user_id, User.is_deleted == 0)
                        )
                        user = result.scalar_one_or_none()

    # 2. X-API-Key
    if user is None and x_api_key:
        key_hash = derive_api_key_hash(x_api_key)
        result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None)))
        api_key = result.scalar_one_or_none()
        if api_key:
            result = await db.execute(
                select(User).where(User.uid == api_key.uid, User.is_deleted == 0)
            )
            user = result.scalar_one_or_none()
            if user:
                api_key.last_used_at = utc_now_naive()

    if user is None:
        raise credentials_exception
    return user


async def get_admin_user(user: User = Depends(get_required_user)) -> User:
    """Restrict access to admin and superadmin users."""
    if user.role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
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
    try:
        numeric_user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    result = await db.execute(select(User).where(User.id == numeric_user_id, User.is_deleted == 0))
    return result.scalar_one_or_none()
