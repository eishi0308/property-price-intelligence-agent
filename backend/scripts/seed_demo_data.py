#!/usr/bin/env python3
"""Load the configured provider's corpus into PostgreSQL.

For the demo provider this ingests the whole fixture set. For a live provider it
ingests the neighbourhood around a given query — live APIs are metered, so we
pull what an analysis actually needs rather than mirroring a vendor's database.

Embeddings are NOT written here; run `scripts/build_embeddings.py` afterwards so
the two concerns (structured ingest vs. semantic index) stay separable.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db import repository
from app.db.engine import dispose_engine, session_scope
from app.db.schema import create_schema
from app.observability import configure_logging, get_logger
from app.providers import build_provider
from app.providers.demo import DemoProvider

logger = get_logger("seed")


async def seed_demo() -> dict[str, int]:
    provider = DemoProvider()
    counts = {"properties": 0, "listings": 0}
    async with session_scope() as session:
        id_map = await repository.upsert_properties(session, provider.all_properties())
        counts["properties"] = len(id_map)
        for listing in provider.all_listings():
            property_id = id_map.get(listing.property_external_id)
            if property_id is None:
                continue
            await repository.upsert_listing(session, listing, property_id)
            counts["listings"] += 1
    return counts


async def seed_live(query: str, radius_km: float, lookback_months: int) -> dict[str, int]:
    provider = build_provider()
    counts = {"properties": 0, "listings": 0}
    try:
        resolution = await provider.resolve_property(query)
        if not resolution.resolved or resolution.property_record is None:
            raise SystemExit(f"Could not resolve '{query}': {resolution.failure_reason}")
        target = resolution.property_record
        if target.coordinates is None:
            raise SystemExit("Provider returned no coordinates; geographic search is impossible.")

        async with session_scope() as session:
            target_id = await repository.upsert_property(session, target)
            counts["properties"] += 1
            current = await provider.get_current_listing(target.external_id)
            if current:
                await repository.upsert_listing(session, current, target_id)
                counts["listings"] += 1

            transactions = await provider.search_sold_transactions(
                centre_latitude=target.coordinates.latitude,
                centre_longitude=target.coordinates.longitude,
                radius_km=radius_km,
                sold_since=date.today() - timedelta(days=int(lookback_months * 30.44)),
                limit=200,
            )
            for transaction in transactions:
                property_id = await repository.upsert_property(session, transaction.property_record)
                counts["properties"] += 1
                listing = transaction.listing
                if listing is None:
                    listing = await provider.get_historical_listing(transaction.external_id)
                if listing:
                    await repository.upsert_listing(session, listing, property_id)
                    counts["listings"] += 1
    finally:
        await provider.close()
    return counts


async def main_async(args: argparse.Namespace) -> None:
    configure_logging()
    settings = get_settings()
    await create_schema(drop_first=args.reset)
    if args.reset:
        logger.info("seed.schema_reset")

    if settings.property_provider.value == "demo":
        counts = await seed_demo()
    else:
        counts = await seed_live(args.query, args.radius_km, args.lookback_months)

    async with session_scope() as session:
        total = await repository.count_properties(session)
    logger.info(
        "seed.complete", provider=settings.property_provider.value, **counts, properties_in_db=total
    )
    print(
        f"Seeded {counts['properties']} properties and {counts['listings']} listings "
        f"from provider '{settings.property_provider.value}'. Database now holds {total} properties."
    )
    print("Next: python scripts/build_embeddings.py")
    await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed PostgreSQL from the configured property provider."
    )
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all tables first.")
    parser.add_argument("--query", default="12/45 Redmyre Road, Strathfield NSW 2135")
    parser.add_argument("--radius-km", type=float, default=5.0)
    parser.add_argument("--lookback-months", type=int, default=18)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
