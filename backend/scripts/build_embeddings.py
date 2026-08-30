#!/usr/bin/env python3
"""Populate `evidence_chunks` and its pgvector index.

Builds four retrieval domains, each embedded separately so RAG can query them
independently:

    listing_description  one chunk per property, from its listing text
    property_summary     structured attributes as prose (citable, deterministic)
    market_context       suburb-level statistics and commentary
    location_context     suburb narrative

Re-runnable: writes are keyed on (source_type, source_id) and replace in place.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import get_settings
from app.db import repository
from app.db.engine import dispose_engine, session_scope
from app.db.models import Listing, Property
from app.observability import configure_logging, get_logger
from app.providers import build_provider
from app.retrieval.embeddings import (
    embedding_model_name,
    embedding_quality_note,
    get_embeddings,
)
from app.retrieval.semantic_text import build_semantic_document
from app.retrieval.vector_store import (
    SOURCE_LISTING,
    SOURCE_LOCATION,
    SOURCE_MARKET,
    SOURCE_PROPERTY,
)
from app.schemas.property import (
    Coordinates,
    ListingRecord,
    ListingStatus,
    PropertyRecord,
    PropertyType,
)

logger = get_logger("embeddings")
BATCH_SIZE = 64


def _to_record(row: Property) -> PropertyRecord:
    coordinates = (
        Coordinates(latitude=row.latitude, longitude=row.longitude)
        if row.latitude is not None and row.longitude is not None
        else None
    )
    return PropertyRecord(
        external_id=row.external_id,
        provider=row.provider,
        address=row.address,
        suburb=row.suburb,
        state=row.state,
        postcode=row.postcode,
        coordinates=coordinates,
        property_type=PropertyType(row.property_type),
        bedrooms=row.bedrooms,
        bathrooms=row.bathrooms,
        carspaces=row.carspaces,
        floor_area_sqm=row.floor_area,
        land_area_sqm=row.land_area,
        year_built=row.year_built,
        is_demo_data=row.is_demo_data,
    )


def _to_listing(row: Listing) -> ListingRecord:
    return ListingRecord(
        external_listing_id=row.external_listing_id,
        property_external_id="",
        provider=row.provider,
        status=ListingStatus(row.status),
        asking_price=row.asking_price,
        price_guide_text=row.price_guide_text,
        description=row.description,
        headline=row.headline,
        listed_at=row.listed_at,
        sold_at=row.sold_at,
        sold_price=row.sold_price,
        source_url=row.source_url,
        is_demo_data=row.is_demo_data,
    )


def _attribute_prose(record: PropertyRecord) -> str:
    bits = [
        f"{record.address} is a {record.property_type.value} in {record.suburb} {record.state}."
    ]
    known = []
    if record.bedrooms is not None:
        known.append(f"{record.bedrooms} bedrooms")
    if record.bathrooms is not None:
        known.append(f"{record.bathrooms} bathrooms")
    if record.carspaces is not None:
        known.append(f"{record.carspaces} car spaces")
    if record.floor_area_sqm:
        known.append(f"{record.floor_area_sqm:g} m² internal area")
    if record.land_area_sqm:
        known.append(f"{record.land_area_sqm:g} m² land")
    bits.append("Recorded attributes: " + (", ".join(known) if known else "none recorded") + ".")
    unknown = [
        name
        for name, value in (
            ("bedrooms", record.bedrooms),
            ("bathrooms", record.bathrooms),
            ("car spaces", record.carspaces),
            ("internal area", record.floor_area_sqm),
        )
        if value is None
    ]
    if unknown:
        bits.append(f"Not recorded by the provider: {', '.join(unknown)}.")
    return " ".join(bits)


async def build(reset: bool) -> dict[str, int]:
    settings = get_settings()
    embeddings = get_embeddings()
    model = embedding_model_name(settings)
    counts = {"listing": 0, "property": 0, "market": 0, "location": 0, "degraded": 0}

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Property, Listing).outerjoin(
                    Listing,
                    (Listing.property_id == Property.id)
                    & (Listing.status.in_(("sold", "current"))),
                )
            )
        ).all()

        pending: list[tuple[str, str, str, dict, Property]] = []
        for property_row, listing_row in rows:
            record = _to_record(property_row)
            listing = _to_listing(listing_row) if listing_row else None
            document = build_semantic_document(record, listing)
            if document.is_degraded:
                counts["degraded"] += 1
            pending.append(
                (
                    SOURCE_LISTING,
                    f"{property_row.provider}:{property_row.external_id}",
                    document.text,
                    {
                        "concepts": document.concepts,
                        "text_source": document.source,
                        "degraded": document.is_degraded,
                        "address": record.address,
                        "suburb": record.suburb,
                    },
                    property_row,
                )
            )
            pending.append(
                (
                    SOURCE_PROPERTY,
                    f"attrs:{property_row.provider}:{property_row.external_id}",
                    _attribute_prose(record),
                    {"address": record.address, "suburb": record.suburb},
                    property_row,
                )
            )

        for start in range(0, len(pending), BATCH_SIZE):
            batch = pending[start : start + BATCH_SIZE]
            vectors = await embeddings.aembed_documents([item[2] for item in batch])
            for (source_type, source_id, text, metadata, property_row), vector in zip(
                batch, vectors, strict=True
            ):
                await repository.replace_evidence_chunk(
                    session,
                    source_type=source_type,
                    source_id=source_id,
                    content=text,
                    embedding=vector,
                    embedding_model=model,
                    property_id=property_row.id,
                    title=property_row.address,
                    metadata=metadata,
                    source_url=None,
                    is_demo_data=property_row.is_demo_data,
                )
                counts["listing" if source_type == SOURCE_LISTING else "property"] += 1

    # --- market + location domains come from the provider, not the DB ----------
    provider = build_provider()
    try:
        market_contexts = (
            provider.all_market_context() if hasattr(provider, "all_market_context") else []
        )
        location_rows = provider.location_context() if hasattr(provider, "location_context") else []
    finally:
        await provider.close()

    async with session_scope() as session:
        if market_contexts:
            texts = [
                (
                    f"{context.suburb} {context.state} {context.postcode} — "
                    f"{(context.property_type.value if context.property_type else 'all')} market. "
                    f"Median sold price {f'${context.median_sold_price:,}' if context.median_sold_price else 'not available'} "
                    f"({context.median_price_period or 'period not stated'}). "
                    f"Sales volume (12 months): {context.sales_volume_12m if context.sales_volume_12m is not None else 'not available'}. "
                    f"Median days on market: {context.median_days_on_market if context.median_days_on_market is not None else 'not available'}. "
                    f"12-month price change: {f'{context.price_change_12m_pct}%' if context.price_change_12m_pct is not None else 'not available'}. "
                    f"{context.commentary or ''}"
                ).strip()
                for context in market_contexts
            ]
            vectors = await embeddings.aembed_documents(texts)
            for context, text, vector in zip(market_contexts, texts, vectors, strict=True):
                await repository.replace_evidence_chunk(
                    session,
                    source_type=SOURCE_MARKET,
                    source_id=f"{context.suburb}:{context.state}:{context.property_type.value if context.property_type else 'all'}",
                    content=text,
                    embedding=vector,
                    embedding_model=model,
                    property_id=None,
                    title=f"{context.suburb} {(context.property_type.value if context.property_type else 'all')} market",
                    metadata={
                        "suburb": context.suburb,
                        "state": context.state,
                        "property_type": context.property_type.value
                        if context.property_type
                        else None,
                        "median_sold_price": context.median_sold_price,
                    },
                    published_at=context.as_at,
                    is_demo_data=context.is_demo_data,
                )
                counts["market"] += 1

        if location_rows:
            texts = [row["content"] for row in location_rows]
            vectors = await embeddings.aembed_documents(texts)
            for row, text, vector in zip(location_rows, texts, vectors, strict=True):
                await repository.replace_evidence_chunk(
                    session,
                    source_type=SOURCE_LOCATION,
                    source_id=f"{row['suburb']}:{row['state']}",
                    content=text,
                    embedding=vector,
                    embedding_model=model,
                    property_id=None,
                    title=f"{row['suburb']} location profile",
                    metadata={"suburb": row["suburb"], "state": row["state"]},
                    is_demo_data=True,
                )
                counts["location"] += 1
    return counts


async def main_async(args: argparse.Namespace) -> None:
    configure_logging()
    counts = await build(args.reset)
    async with session_scope() as session:
        embedded = await repository.count_embedded_chunks(session)
    print("\nEMBEDDING INDEX BUILT")
    print(f"  model                 {embedding_model_name()}")
    print(f"  listing chunks        {counts['listing']}")
    print(f"    of which degraded   {counts['degraded']}  (no usable description; attributes only)")
    print(f"  property summaries    {counts['property']}")
    print(f"  market context        {counts['market']}")
    print(f"  location context      {counts['location']}")
    print(f"  total embedded rows   {embedded}")
    print(f"\n  {embedding_quality_note()}\n")
    await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the pgvector semantic index.")
    parser.add_argument(
        "--reset", action="store_true", help="(chunks are replaced in place regardless)"
    )
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
