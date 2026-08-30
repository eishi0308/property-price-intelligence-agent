"""Shared fixtures.

Integration tests need a real PostgreSQL with pgvector and the demo corpus loaded
(`python scripts/seed_demo_data.py --reset && python scripts/build_embeddings.py`).
Rather than failing with an obscure connection error, they skip with an actionable
message when that is not available, so the unit suite stays runnable anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import repository
from app.db.engine import pgvector_available, ping, session_scope
from app.observability import configure_logging
from app.providers.demo import DemoProvider

TARGET_QUERY = "12/45 Redmyre Road, Strathfield NSW 2135"
TARGET_URL = "https://www.realestate.com.au/property-apartment-nsw-strathfield-145820394"

configure_logging(level="WARNING")


@pytest.fixture(scope="session")
def demo_provider() -> DemoProvider:
    return DemoProvider()


@pytest.fixture(scope="session")
async def database_ready() -> bool:
    """Skip integration tests cleanly when the database is not usable."""
    if not await ping():
        pytest.skip("PostgreSQL is unreachable; set DATABASE_URL and start the database.")
    if not await pgvector_available():
        pytest.skip("pgvector extension is not installed in the configured database.")
    async with session_scope() as session:
        count = await repository.count_properties(session)
        embedded = await repository.count_embedded_chunks(session)
    if count == 0:
        pytest.skip("No properties in the database; run scripts/seed_demo_data.py --reset.")
    if embedded == 0:
        pytest.skip("No embeddings in the database; run scripts/build_embeddings.py.")
    return True


@pytest.fixture
async def target_property(demo_provider: DemoProvider):
    resolution = await demo_provider.resolve_property(TARGET_QUERY)
    assert resolution.property_record is not None
    return resolution.property_record
