"""Data-access helpers.

Deliberately thin and explicit: no lazy-loading surprises, and every read the
retrieval pipeline performs is a query written here where it can be reviewed.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentTrace,
    Analysis,
    AnalysisEvidence,
    ComparableResult,
    EvidenceChunk,
    Listing,
    Property,
)
from app.schemas.property import ListingRecord, PropertyRecord


# ---------------------------------------------------------------------------
# Properties & listings
# ---------------------------------------------------------------------------
async def upsert_property(session: AsyncSession, record: PropertyRecord) -> uuid.UUID:
    """Insert or refresh one property, keyed on (provider, external_id)."""
    values = {
        "external_id": record.external_id,
        "provider": record.provider,
        "address": record.address,
        "suburb": record.suburb,
        "state": record.state,
        "postcode": record.postcode,
        "latitude": record.coordinates.latitude if record.coordinates else None,
        "longitude": record.coordinates.longitude if record.coordinates else None,
        "property_type": record.property_type.value,
        "bedrooms": record.bedrooms,
        "bathrooms": record.bathrooms,
        "carspaces": record.carspaces,
        "floor_area": record.floor_area_sqm,
        "land_area": record.land_area_sqm,
        "year_built": record.year_built,
        "is_demo_data": record.is_demo_data,
    }
    statement = (
        pg_insert(Property)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[Property.provider, Property.external_id],
            set_={
                key: value
                for key, value in values.items()
                if key not in ("provider", "external_id")
            },
        )
        .returning(Property.id)
    )
    result = await session.execute(statement)
    return result.scalar_one()


async def upsert_properties(
    session: AsyncSession, records: Iterable[PropertyRecord]
) -> dict[str, uuid.UUID]:
    mapping: dict[str, uuid.UUID] = {}
    for record in records:
        mapping[record.external_id] = await upsert_property(session, record)
    return mapping


async def upsert_listing(
    session: AsyncSession, record: ListingRecord, property_id: uuid.UUID
) -> uuid.UUID:
    values = {
        "property_id": property_id,
        "external_listing_id": record.external_listing_id,
        "provider": record.provider,
        "status": record.status.value,
        "asking_price": record.asking_price,
        "price_guide_text": record.price_guide_text,
        "headline": record.headline,
        "description": record.description,
        "listed_at": record.listed_at,
        "sold_at": record.sold_at,
        "sold_price": record.sold_price,
        "source": record.provider,
        "source_url": record.source_url,
        "is_demo_data": record.is_demo_data,
    }
    statement = (
        pg_insert(Listing)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[Listing.provider, Listing.external_listing_id],
            set_={
                key: value
                for key, value in values.items()
                if key not in ("provider", "external_listing_id")
            },
        )
        .returning(Listing.id)
    )
    result = await session.execute(statement)
    return result.scalar_one()


async def get_property_by_external_id(
    session: AsyncSession, provider: str, external_id: str
) -> Property | None:
    result = await session.execute(
        select(Property).where(Property.provider == provider, Property.external_id == external_id)
    )
    return result.scalar_one_or_none()


async def get_property(session: AsyncSession, property_id: uuid.UUID) -> Property | None:
    return await session.get(Property, property_id)


async def get_properties(
    session: AsyncSession, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Property]:
    if not ids:
        return {}
    result = await session.execute(select(Property).where(Property.id.in_(ids)))
    return {row.id: row for row in result.scalars()}


async def latest_sold_listing(session: AsyncSession, property_id: uuid.UUID) -> Listing | None:
    result = await session.execute(
        select(Listing)
        .where(Listing.property_id == property_id, Listing.status == "sold")
        .order_by(Listing.sold_at.desc().nullslast())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def current_listing(session: AsyncSession, property_id: uuid.UUID) -> Listing | None:
    result = await session.execute(
        select(Listing)
        .where(Listing.property_id == property_id, Listing.status == "current")
        .order_by(Listing.listed_at.desc().nullslast())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def sold_listings_for(
    session: AsyncSession, property_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Listing]:
    """Most recent sold listing per property, in one query."""
    if not property_ids:
        return {}
    ranked = (
        select(
            Listing,
            func.row_number()
            .over(partition_by=Listing.property_id, order_by=Listing.sold_at.desc().nullslast())
            .label("rank"),
        )
        .where(Listing.property_id.in_(property_ids), Listing.status == "sold")
        .subquery()
    )
    aliased = select(Listing).from_statement(
        select(ranked).where(ranked.c.rank == 1)  # type: ignore[arg-type]
    )
    result = await session.execute(aliased)
    return {row.property_id: row for row in result.scalars()}


async def count_properties(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(Property))
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Evidence chunks
# ---------------------------------------------------------------------------
async def replace_evidence_chunk(
    session: AsyncSession,
    *,
    source_type: str,
    source_id: str,
    content: str,
    embedding: list[float] | None,
    embedding_model: str | None,
    property_id: uuid.UUID | None = None,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
    source_url: str | None = None,
    published_at: date | None = None,
    is_demo_data: bool = False,
) -> uuid.UUID:
    """Idempotent write keyed on (source_type, source_id)."""
    await session.execute(
        delete(EvidenceChunk).where(
            EvidenceChunk.source_type == source_type, EvidenceChunk.source_id == source_id
        )
    )
    chunk = EvidenceChunk(
        property_id=property_id,
        source_type=source_type,
        source_id=source_id,
        title=title,
        content=content,
        chunk_metadata=metadata or {},
        embedding=embedding,
        embedding_model=embedding_model,
        source_url=source_url,
        published_at=published_at,
        is_demo_data=is_demo_data,
    )
    session.add(chunk)
    await session.flush()
    return chunk.id


async def count_embedded_chunks(session: AsyncSession, source_type: str | None = None) -> int:
    statement = (
        select(func.count()).select_from(EvidenceChunk).where(EvidenceChunk.embedding.isnot(None))
    )
    if source_type:
        statement = statement.where(EvidenceChunk.source_type == source_type)
    result = await session.execute(statement)
    return int(result.scalar_one())


async def chunks_missing_embeddings(session: AsyncSession, limit: int = 500) -> list[EvidenceChunk]:
    result = await session.execute(
        select(EvidenceChunk).where(EvidenceChunk.embedding.is_(None)).limit(limit)
    )
    return list(result.scalars())


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------
async def create_analysis(
    session: AsyncSession, *, query: str, user_id: str | None = None
) -> Analysis:
    analysis = Analysis(query=query, user_id=user_id, status="pending")
    session.add(analysis)
    await session.flush()
    return analysis


async def get_analysis(session: AsyncSession, analysis_id: uuid.UUID) -> Analysis | None:
    return await session.get(Analysis, analysis_id)


async def list_analyses(session: AsyncSession, limit: int = 25) -> list[Analysis]:
    result = await session.execute(
        select(Analysis).order_by(Analysis.created_at.desc()).limit(limit)
    )
    return list(result.scalars())


async def update_analysis(session: AsyncSession, analysis_id: uuid.UUID, **fields: Any) -> None:
    if not fields:
        return
    await session.execute(update(Analysis).where(Analysis.id == analysis_id).values(**fields))


async def replace_comparables(
    session: AsyncSession, analysis_id: uuid.UUID, rows: Sequence[dict[str, Any]]
) -> None:
    """Rewrite the comparable set, preserving any user exclusion decisions."""
    existing = await session.execute(
        select(
            ComparableResult.comparable_property_id,
            ComparableResult.excluded_by_user,
            ComparableResult.exclusion_reason,
        ).where(
            ComparableResult.analysis_id == analysis_id, ComparableResult.excluded_by_user.is_(True)
        )
    )
    user_excluded = {row[0]: row[2] for row in existing}

    await session.execute(
        delete(ComparableResult).where(ComparableResult.analysis_id == analysis_id)
    )
    for row in rows:
        property_id = row["comparable_property_id"]
        if property_id in user_excluded:
            row = {
                **row,
                "included": False,
                "excluded_by_user": True,
                "exclusion_reason": user_excluded[property_id] or "Excluded by user",
            }
        session.add(ComparableResult(analysis_id=analysis_id, **row))
    await session.flush()


async def get_comparables(session: AsyncSession, analysis_id: uuid.UUID) -> list[ComparableResult]:
    result = await session.execute(
        select(ComparableResult)
        .where(ComparableResult.analysis_id == analysis_id)
        .order_by(ComparableResult.final_rank.asc().nullslast())
    )
    return list(result.scalars())


async def set_comparable_exclusion(
    session: AsyncSession,
    analysis_id: uuid.UUID,
    comparable_property_id: uuid.UUID,
    *,
    excluded: bool,
    reason: str | None,
) -> bool:
    result = await session.execute(
        update(ComparableResult)
        .where(
            ComparableResult.analysis_id == analysis_id,
            ComparableResult.comparable_property_id == comparable_property_id,
        )
        .values(
            excluded_by_user=excluded,
            included=not excluded,
            exclusion_reason=(reason or "Excluded by user") if excluded else None,
        )
        .returning(ComparableResult.id)
    )
    return result.first() is not None


async def replace_analysis_evidence(
    session: AsyncSession, analysis_id: uuid.UUID, rows: Sequence[dict[str, Any]]
) -> None:
    await session.execute(
        delete(AnalysisEvidence).where(AnalysisEvidence.analysis_id == analysis_id)
    )
    for row in rows:
        session.add(AnalysisEvidence(analysis_id=analysis_id, **row))
    await session.flush()


async def get_analysis_evidence(
    session: AsyncSession, analysis_id: uuid.UUID
) -> list[AnalysisEvidence]:
    result = await session.execute(
        select(AnalysisEvidence).where(AnalysisEvidence.analysis_id == analysis_id)
    )
    return list(result.scalars())


async def replace_traces(
    session: AsyncSession, analysis_id: uuid.UUID, entries: Sequence[dict[str, Any]]
) -> None:
    await session.execute(delete(AgentTrace).where(AgentTrace.analysis_id == analysis_id))
    for entry in entries:
        session.add(AgentTrace(analysis_id=analysis_id, **entry))
    await session.flush()


async def get_traces(session: AsyncSession, analysis_id: uuid.UUID) -> list[AgentTrace]:
    result = await session.execute(
        select(AgentTrace)
        .where(AgentTrace.analysis_id == analysis_id)
        .order_by(AgentTrace.sequence)
    )
    return list(result.scalars())
