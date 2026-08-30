"""Assemble the evidence the assessment is allowed to rely on.

RAG here is not "stuff some documents in the prompt". It answers a specific
question — *why does this asking price look reasonable or unreasonable?* — and
the answer needs four different kinds of support, so there are four retrieval
domains, each queried differently:

    comparable   the listing text of the sales actually selected. Retrieved by
                 identity, not similarity: these are the sales the pipeline
                 already committed to, and swapping in a "more similar" listing
                 at this point would decouple the narrative from the numbers.

    property     the target's own listing text and recorded attributes, so any
                 claimed premium ("renovated", "parking") traces to the listing.

    market       suburb-level statistics. Retrieved semantically and filtered by
                 suburb, because the useful chunk is the one about *this* market
                 segment, not the highest-scoring text overall.

    location     suburb narrative explaining intra-suburb price drivers, which is
                 what lets the assessment say *why* a main-road comparable sold
                 lower without inventing a reason.

Every item carries a citation id, a source and a "why it matters" line. The
narration step may only cite these ids; the guardrail layer rejects any other
citation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvidenceChunk
from app.observability import TraceRecorder, get_logger
from app.retrieval.vector_store import (
    SOURCE_LISTING,
    SOURCE_LOCATION,
    SOURCE_MARKET,
    SOURCE_PROPERTY,
    similarity_search,
)
from app.schemas.property import PropertyRecord

logger = get_logger(__name__)

MAX_MARKET_ITEMS = 3
MAX_LOCATION_ITEMS = 2


@dataclass
class EvidenceItem:
    citation_id: str
    source_type: str
    title: str
    content: str
    why_it_matters: str
    source: str
    chunk_id: uuid.UUID | None = None
    property_id: uuid.UUID | None = None
    source_url: str | None = None
    published_at: date | None = None
    similarity: float | None = None
    is_demo_data: bool = False

    def for_prompt(self) -> str:
        published = f" (as at {self.published_at})" if self.published_at else ""
        return f"[{self.citation_id}] {self.title}{published}\n{self.content.strip()}"


@dataclass
class EvidenceBundle:
    items: list[EvidenceItem] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def citation_ids(self) -> set[str]:
        return {item.citation_id for item in self.items}

    def by_type(self, source_type: str) -> list[EvidenceItem]:
        return [item for item in self.items if item.source_type == source_type]

    def to_prompt_block(self) -> str:
        if not self.items:
            return "NO EVIDENCE WAS RETRIEVED."
        return "\n\n".join(item.for_prompt() for item in self.items)

    def coverage(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.source_type] = counts.get(item.source_type, 0) + 1
        return counts

    @property
    def is_sufficient(self) -> bool:
        """Comparable evidence is mandatory; market/location strengthen it."""
        coverage = self.coverage()
        return coverage.get(SOURCE_LISTING, 0) >= 3


async def _chunks_for_properties(
    session: AsyncSession, property_ids: list[uuid.UUID], source_type: str
) -> dict[uuid.UUID, EvidenceChunk]:
    if not property_ids:
        return {}
    rows = (
        await session.execute(
            select(EvidenceChunk).where(
                EvidenceChunk.property_id.in_(property_ids),
                EvidenceChunk.source_type == source_type,
            )
        )
    ).scalars()
    return {row.property_id: row for row in rows if row.property_id}


async def retrieve_supporting_evidence(
    session: AsyncSession,
    *,
    target: PropertyRecord,
    target_property_id: uuid.UUID | None,
    included_property_ids: list[uuid.UUID],
    comparable_labels: dict[uuid.UUID, str],
    target_embedding: list[float],
    include_market: bool = True,
    include_location: bool = True,
    tracer: TraceRecorder | None = None,
) -> EvidenceBundle:
    """Gather grounded evidence across all four domains."""
    items: list[EvidenceItem] = []
    missing: list[str] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"E{counter}"

    # --- Domain 1: the target property's own listing --------------------------
    if target_property_id:
        target_chunks = await _chunks_for_properties(session, [target_property_id], SOURCE_LISTING)
        chunk = target_chunks.get(target_property_id)
        if chunk:
            items.append(
                EvidenceItem(
                    citation_id=next_id(),
                    source_type=SOURCE_PROPERTY,
                    title=f"Target listing — {target.address}",
                    content=chunk.content,
                    why_it_matters=(
                        "Establishes which features of the target are actually advertised, so any "
                        "claimed premium can be traced to the listing rather than assumed."
                    ),
                    source=target.provider,
                    chunk_id=chunk.id,
                    property_id=target_property_id,
                    source_url=chunk.source_url,
                    is_demo_data=chunk.is_demo_data,
                )
            )
        else:
            missing.append("The target property's listing description was not available.")
    else:
        missing.append("The target property is not indexed, so its listing text is unavailable.")

    # --- Domain 2: the selected comparables -----------------------------------
    comparable_chunks = await _chunks_for_properties(session, included_property_ids, SOURCE_LISTING)
    for property_id in included_property_ids:
        chunk = comparable_chunks.get(property_id)
        label = comparable_labels.get(property_id, "comparable sale")
        if chunk is None:
            missing.append(f"No listing description was available for {label}.")
            continue
        items.append(
            EvidenceItem(
                citation_id=next_id(),
                source_type=SOURCE_LISTING,
                title=f"Comparable sale — {label}",
                content=chunk.content,
                why_it_matters=(
                    "A sale actually used to build the evidence range; its wording is the basis "
                    "for any stated similarity or difference."
                ),
                source=chunk.chunk_metadata.get("source", "provider")
                if chunk.chunk_metadata
                else "provider",
                chunk_id=chunk.id,
                property_id=property_id,
                source_url=chunk.source_url,
                similarity=None,
                is_demo_data=chunk.is_demo_data,
            )
        )

    # --- Domain 3: market context ---------------------------------------------
    if include_market:
        market_hits = await similarity_search(
            session, target_embedding, source_types=[SOURCE_MARKET], k=12
        )
        relevant = [
            hit
            for hit in market_hits
            if str(hit.metadata.get("suburb", "")).lower() == target.suburb.lower()
        ] or market_hits[:MAX_MARKET_ITEMS]
        if not relevant:
            missing.append("No suburb market context was available for this location.")
        for hit in relevant[:MAX_MARKET_ITEMS]:
            items.append(
                EvidenceItem(
                    citation_id=next_id(),
                    source_type=SOURCE_MARKET,
                    title=hit.title or "Market context",
                    content=hit.content,
                    why_it_matters=(
                        "Shows whether the comparable set sits inside or outside the broader "
                        "market for this suburb and property type."
                    ),
                    source=hit.metadata.get("source", "provider"),
                    chunk_id=hit.chunk_id,
                    similarity=hit.similarity,
                    is_demo_data=hit.is_demo_data,
                )
            )

    # --- Domain 4: location context -------------------------------------------
    if include_location:
        location_hits = await similarity_search(
            session, target_embedding, source_types=[SOURCE_LOCATION], k=8
        )
        relevant = [
            hit
            for hit in location_hits
            if str(hit.metadata.get("suburb", "")).lower() == target.suburb.lower()
        ][:MAX_LOCATION_ITEMS]
        if not relevant:
            missing.append("No location profile was available for this suburb.")
        for hit in relevant:
            items.append(
                EvidenceItem(
                    citation_id=next_id(),
                    source_type=SOURCE_LOCATION,
                    title=hit.title or "Location profile",
                    content=hit.content,
                    why_it_matters=(
                        "Explains intra-suburb price drivers, so differences between comparables "
                        "can be attributed to position rather than guessed at."
                    ),
                    source=hit.metadata.get("source", "provider"),
                    chunk_id=hit.chunk_id,
                    similarity=hit.similarity,
                    is_demo_data=hit.is_demo_data,
                )
            )

    bundle = EvidenceBundle(items=items, missing=missing)
    if tracer:
        tracer.record(
            "retrieval",
            "retrieve_supporting_evidence",
            items=len(items),
            coverage=bundle.coverage(),
            missing=len(missing),
        )
    logger.info("rag.evidence", coverage=bundle.coverage(), missing=len(missing))
    return bundle
