"""用户数据访问层（datadeck 最小版，自 Yuxi user_repository 抽取）。

仅实现 P2B Skills 链所需接口；完整用户管理 CRUD 在 auth_router 中直接实现。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import session_context
from server.models import User


class UserRepository:
    """用户数据访问层"""

    def __init__(self, db_session: AsyncSession | None = None):
        self.db_session = db_session

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        """复用请求会话，未注入时创建独立事务会话。"""
        if self.db_session is not None:
            yield self.db_session
            return
        async with session_context() as session:
            yield session

    async def get_by_uid_with_db(self, db: AsyncSession, uid: str) -> User | None:
        """使用指定的 db 获取用户"""
        result = await db.execute(select(User).where(User.uid == uid))
        return result.scalar_one_or_none()

    async def get_by_uid(self, uid: str) -> User | None:
        """根据 uid 获取用户"""
        async with self._session() as session:
            return await self.get_by_uid_with_db(session, uid)

    async def list_by_uids_with_db(self, db: AsyncSession, uids: list[str]) -> list[User]:
        """使用指定的 db 批量获取指定 uid 的用户。"""
        normalized_uids = sorted({str(uid).strip() for uid in uids if str(uid).strip()})
        if not normalized_uids:
            return []
        result = await db.execute(select(User).where(User.uid.in_(normalized_uids)))
        return list(result.scalars().all())
