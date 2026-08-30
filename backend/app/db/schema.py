"""Schema creation / teardown.

The ORM metadata in `models.py` is the single source of truth; this module just
applies it, plus the `vector` extension which must exist before the pgvector
columns can be created.
"""

from __future__ import annotations

from sqlalchemy import text

from app.db.engine import get_engine
from app.db.models import Base


async def create_schema(drop_first: bool = False) -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        if drop_first:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def drop_schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
