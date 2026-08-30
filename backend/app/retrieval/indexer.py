"""On-demand semantic indexing.

`scripts/build_embeddings.py` handles bulk indexing, but the agent can discover
properties mid-run (when it widens the search radius, new sales land in the
database). Those rows must be embedded before the semantic arm can see them,
otherwise widening the search would silently *reduce* semantic coverage — the
worst kind of bug, because the pipeline still returns a confident answer.

Only missing chunks are embedded, so this is cheap on the common path.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repository
from app.db.models import EvidenceChunk, Listing, Property
from app.observability import get_logger
from app.retrieval.embeddings import embedding_model_name, get_embeddings
from app.retrieval.semantic_text import build_semantic_document
from app.retrieval.vector_store import SOURCE_LISTING
from app.schemas.property import (
    Coordinates,
    ListingRecord,
    ListingStatus,
    PropertyRecord,
    PropertyType,
)

logger = get_logger(__name__)
BATCH_SIZE = 64


def property_to_record(row: Property) -> PropertyRecord:
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


def listing_to_record(row: Listing, property_external_id: str = "") -> ListingRecord:
    return ListingRecord(
        external_listing_id=row.external_listing_id,
        property_external_id=property_external_id,
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


async def ensure_indexed(
    session: AsyncSession, property_ids: Sequence[uuid.UUID]
) -> tuple[int, int]:
    """Embed any of `property_ids` that have no listing chunk yet.

    Returns `(newly_indexed, degraded)` where `degraded` counts properties whose
    chunk had to be built from structured attributes because no listing
    description existed.
    """
    if not property_ids:
        return 0, 0

    existing = set(
        (
            await session.execute(
                select(EvidenceChunk.property_id).where(
                    EvidenceChunk.property_id.in_(list(property_ids)),
                    EvidenceChunk.source_type == SOURCE_LISTING,
                )
            )
        ).scalars()
    )
    missing = [property_id for property_id in property_ids if property_id not in existing]
    if not missing:
        return 0, 0

    rows = (
        await session.execute(
            select(Property, Listing)
            .outerjoin(
                Listing,
                (Listing.property_id == Property.id) & (Listing.status.in_(("sold", "current"))),
            )
            .where(Property.id.in_(missing))
        )
    ).all()

    embeddings = get_embeddings()
    model = embedding_model_name()
    pending: list[tuple[Property, str, dict]] = []
    degraded = 0
    seen: set[uuid.UUID] = set()

    for property_row, listing_row in rows:
        if property_row.id in seen:
            continue
        seen.add(property_row.id)
        record = property_to_record(property_row)
        listing = listing_to_record(listing_row, record.external_id) if listing_row else None
        document = build_semantic_document(record, listing)
        if document.is_degraded:
            degraded += 1
        pending.append(
            (
                property_row,
                document.text,
                {
                    "concepts": document.concepts,
                    "text_source": document.source,
                    "degraded": document.is_degraded,
                    "address": record.address,
                    "suburb": record.suburb,
                },
            )
        )

    indexed = 0
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        vectors = await embeddings.aembed_documents([item[1] for item in batch])
        for (property_row, text, metadata), vector in zip(batch, vectors, strict=True):
            await repository.replace_evidence_chunk(
                session,
                source_type=SOURCE_LISTING,
                source_id=f"{property_row.provider}:{property_row.external_id}",
                content=text,
                embedding=vector,
                embedding_model=model,
                property_id=property_row.id,
                title=property_row.address,
                metadata=metadata,
                is_demo_data=property_row.is_demo_data,
            )
            indexed += 1

    logger.info("index.on_demand", requested=len(property_ids), indexed=indexed, degraded=degraded)
    return indexed, degraded
