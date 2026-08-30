"""STAGE C — lexical retrieval with PostgreSQL full-text search.

Dense retrieval is bad at exactly the things buyers are most literal about:
"north-facing", "lock-up garage", "water views", a street name, a strata levy
figure. Embeddings blur these into a neighbourhood of related meaning; a buyer
filtering for parking does not want "related to parking".

`ts_rank_cd` over the STORED `listings.search_vector` column supplies that
precision. It is the counterweight to the vector arm, not a redundant copy of it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing, Property
from app.observability import get_logger
from app.retrieval.concepts import detect_concepts, keyword_terms_for

logger = get_logger(__name__)


@dataclass
class KeywordHit:
    property_id: uuid.UUID
    listing_id: uuid.UUID
    score: float
    matched_terms: list[str]


def build_tsquery_terms(text: str) -> list[str]:
    """Explicit search terms implied by the concepts present in the target text."""
    concepts = detect_concepts(text)
    terms = keyword_terms_for(concepts)
    # Deduplicate while preserving order, and drop multi-word terms that
    # websearch_to_tsquery would treat as a phrase requirement that is too strict.
    seen: set[str] = set()
    ordered: list[str] = []
    for term in terms:
        head = term.split()[0]
        if head not in seen and len(head) > 2:
            seen.add(head)
            ordered.append(head)
    return ordered


async def keyword_search(
    session: AsyncSession,
    query_text: str,
    *,
    property_ids: Sequence[uuid.UUID] | None = None,
    k: int = 30,
) -> list[KeywordHit]:
    """Rank candidate listings by full-text relevance to the target's concepts."""
    terms = build_tsquery_terms(query_text)
    if not terms:
        return []
    # OR semantics: a comparable sharing any explicit concept term is worth ranking.
    tsquery = func.to_tsquery("english", " | ".join(terms))
    rank = func.ts_rank_cd(Listing.search_vector, tsquery)

    statement = (
        select(Listing.property_id, Listing.id, rank.label("score"))
        .join(Property, Property.id == Listing.property_id)
        .where(Listing.search_vector.op("@@")(tsquery), Listing.status == "sold")
        .order_by(rank.desc())
        .limit(k)
    )
    if property_ids is not None:
        if not property_ids:
            return []
        statement = statement.where(Listing.property_id.in_(list(property_ids)))

    rows = (await session.execute(statement)).all()
    hits = [
        KeywordHit(
            property_id=property_id,
            listing_id=listing_id,
            score=round(float(score), 6),
            matched_terms=terms,
        )
        for property_id, listing_id, score in rows
        if score and float(score) > 0
    ]
    logger.info(
        "retrieval.keyword", terms=terms, hits=len(hits), top_score=hits[0].score if hits else None
    )
    return hits
