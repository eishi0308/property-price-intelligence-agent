"""Async engine / session management.

`psycopg` (v3) is used in async mode, which is also what `pgvector.sqlalchemy`
and LangChain's Postgres integrations expect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    session = get_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with session_scope() as session:
        yield session


async def ping() -> bool:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def pgvector_available() -> bool:
    try:
        async with get_engine().connect() as conn:
            result = await conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
            return result.first() is not None
    except Exception:
        return False


async def dispose_engine() -> None:
    await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
