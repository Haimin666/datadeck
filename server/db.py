"""PostgreSQL async 连接管理。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from server.config import settings

engine_options = {"echo": settings.debug, "pool_pre_ping": True}
if os.getenv("DATADECK_TESTING") == "1":
    # TestClient 为每个 fixture 创建新的事件循环，不能跨循环复用 asyncpg 连接池。
    engine_options["poolclass"] = NullPool
engine = create_async_engine(settings.database_url, **engine_options)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def session_context() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    async with engine.begin():
        # 建表（所有模型导入后调用）
        pass


async def close_db() -> None:
    await engine.dispose()
