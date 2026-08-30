"""Tool implementations shared by the MCP servers and the in-process transport.

Written once, exposed twice:

* `property_data_server.py` / `intelligence_server.py` publish them over stdio as
  real MCP tools with JSON schemas;
* `client.InProcessToolset` binds the same functions directly for tests and for
  deployments where spawning a subprocess per request is not wanted.

Both paths run identical code, so "MCP mode" and "in-process mode" cannot drift
apart in behaviour — only in transport. Every function returns plain JSON-safe
dictionaries, because that is what crosses the MCP wire.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from app.config import get_settings
from app.db import repository
from app.db.engine import session_scope
from app.observability import get_logger
from app.providers import build_provider
from app.providers.base import ProviderError
from app.retrieval.embeddings import get_embeddings
from app.retrieval.vector_store import SOURCE_LISTING, SOURCE_MARKET, similarity_search
from app.schemas.property import PropertyType

logger = get_logger(__name__)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    """Tools return structured failures, never raise across the boundary."""
    return {"ok": False, "error": message, **extra}


def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


# ---------------------------------------------------------------------------
# Property-data tools
# ---------------------------------------------------------------------------
async def resolve_property(query: str) -> dict[str, Any]:
    """Resolve a property URL or street address to a known property."""
    provider = build_provider()
    try:
        resolution = await provider.resolve_property(query)
    except ProviderError as exc:
        return _error(f"{type(exc).__name__}: {exc}", query=query)
    finally:
        await provider.close()

    if not resolution.resolved or resolution.property_record is None:
        return _error(
            resolution.failure_reason or "Could not resolve the property.",
            query=query,
            interpreted_as=resolution.interpreted_as,
            candidates=[
                {"external_id": item.external_id, "address": item.address}
                for item in resolution.candidates[:10]
            ],
        )
    record = resolution.property_record
    return _ok(
        interpreted_as=resolution.interpreted_as,
        property=record.model_dump(mode="json"),
        is_demo_data=record.is_demo_data,
    )


async def get_property_details(external_id: str) -> dict[str, Any]:
    """Structured attributes for one property."""
    provider = build_provider()
    try:
        record = await provider.get_property(external_id)
    except ProviderError as exc:
        return _error(f"{type(exc).__name__}: {exc}", external_id=external_id)
    finally:
        await provider.close()
    if record is None:
        return _error("Property not found.", external_id=external_id)
    return _ok(property=record.model_dump(mode="json"), is_demo_data=record.is_demo_data)


async def get_current_listing(external_id: str) -> dict[str, Any]:
    """The live listing and asking price, if the property is on market."""
    provider = build_provider()
    try:
        listing = await provider.get_current_listing(external_id)
    except ProviderError as exc:
        return _error(f"{type(exc).__name__}: {exc}", external_id=external_id)
    finally:
        await provider.close()
    if listing is None:
        return _error("No current listing for this property.", external_id=external_id)
    return _ok(listing=listing.model_dump(mode="json"), is_demo_data=listing.is_demo_data)


async def get_historical_listing(external_id: str) -> dict[str, Any]:
    """The listing as marketed at the time of the most recent sale."""
    provider = build_provider()
    try:
        listing = await provider.get_historical_listing(external_id)
    except ProviderError as exc:
        return _error(f"{type(exc).__name__}: {exc}", external_id=external_id)
    finally:
        await provider.close()
    if listing is None:
        return _error("No historical listing available.", external_id=external_id)
    return _ok(listing=listing.model_dump(mode="json"), is_demo_data=listing.is_demo_data)


async def search_sold_properties(
    latitude: float,
    longitude: float,
    radius_km: float = 3.0,
    lookback_months: int = 12,
    property_types: list[str] | None = None,
    min_bedrooms: int | None = None,
    max_bedrooms: int | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Recent settled sales near a point, persisted for downstream retrieval.

    Widening `radius_km` or `lookback_months` is how the agent recovers from an
    insufficient comparable set.
    """
    settings = get_settings()
    radius_km = min(radius_km, settings.max_radius_km)
    lookback_months = min(lookback_months, settings.max_lookback_months)
    types = None
    if property_types:
        try:
            types = {PropertyType(item) for item in property_types}
        except ValueError as exc:
            return _error(f"Unknown property type: {exc}")

    provider = build_provider()
    try:
        transactions = await provider.search_sold_transactions(
            centre_latitude=latitude,
            centre_longitude=longitude,
            radius_km=radius_km,
            sold_since=date.today() - timedelta(days=int(lookback_months * 30.44)),
            property_types=types,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
            limit=limit,
        )
    except ProviderError as exc:
        return _error(f"{type(exc).__name__}: {exc}")
    finally:
        await provider.close()

    stored = 0
    async with session_scope() as session:
        for transaction in transactions:
            property_id = await repository.upsert_property(session, transaction.property_record)
            if transaction.listing:
                await repository.upsert_listing(session, transaction.listing, property_id)
                stored += 1
    return _ok(
        count=len(transactions),
        persisted_listings=stored,
        radius_km=radius_km,
        lookback_months=lookback_months,
        sales=[
            {
                "external_id": item.external_id,
                "address": item.property_record.address,
                "sold_price": item.sold_price,
                "sold_at": item.sold_at.isoformat(),
                "distance_km": item.distance_km,
                "bedrooms": item.property_record.bedrooms,
                "property_type": item.property_record.property_type.value,
                "has_description": bool(item.description),
            }
            for item in transactions[:60]
        ],
        is_demo_data=any(item.property_record.is_demo_data for item in transactions),
    )


async def get_market_context(
    suburb: str, state: str, postcode: str, property_type: str | None = None
) -> dict[str, Any]:
    """Suburb-level market statistics, used strictly as retrievable evidence."""
    provider = build_provider()
    try:
        context = await provider.get_market_context(
            suburb=suburb,
            state=state,
            postcode=postcode,
            property_type=PropertyType(property_type) if property_type else None,
        )
    except (ProviderError, ValueError) as exc:
        return _error(f"{type(exc).__name__}: {exc}")
    finally:
        await provider.close()
    if context is None:
        return _error("No market context available.", suburb=suburb, state=state)
    return _ok(market_context=context.model_dump(mode="json"), is_demo_data=context.is_demo_data)


# ---------------------------------------------------------------------------
# Intelligence / retrieval tools
# ---------------------------------------------------------------------------
async def search_similar_properties(
    query_text: str, limit: int = 10, suburb: str | None = None
) -> dict[str, Any]:
    """Semantic search over indexed listing descriptions (pgvector cosine)."""
    embeddings = get_embeddings()
    vector = await embeddings.aembed_query(query_text)
    async with session_scope() as session:
        hits = await similarity_search(
            session, vector, source_types=[SOURCE_LISTING], k=max(limit * 3, limit)
        )
    if suburb:
        hits = [
            hit for hit in hits if str(hit.metadata.get("suburb", "")).lower() == suburb.lower()
        ]
    return _ok(
        count=len(hits[:limit]),
        results=[
            {
                "property_id": str(hit.property_id) if hit.property_id else None,
                "title": hit.title,
                "similarity": hit.similarity,
                "concepts": list((hit.metadata.get("concepts") or {}).keys()),
                "excerpt": hit.content[:280],
                "is_demo_data": hit.is_demo_data,
            }
            for hit in hits[:limit]
        ],
    )


async def retrieve_market_evidence(
    query_text: str, suburb: str | None = None, limit: int = 4
) -> dict[str, Any]:
    """Retrieve suburb market-context chunks relevant to a question."""
    embeddings = get_embeddings()
    vector = await embeddings.aembed_query(query_text)
    async with session_scope() as session:
        hits = await similarity_search(session, vector, source_types=[SOURCE_MARKET], k=12)
    if suburb:
        filtered = [
            hit for hit in hits if str(hit.metadata.get("suburb", "")).lower() == suburb.lower()
        ]
        hits = filtered or hits
    if not hits:
        return _error("No market evidence is indexed for this query.", suburb=suburb)
    return _ok(
        count=len(hits[:limit]),
        evidence=[
            {
                "chunk_id": str(hit.chunk_id),
                "title": hit.title,
                "content": hit.content,
                "similarity": hit.similarity,
                "suburb": hit.metadata.get("suburb"),
                "is_demo_data": hit.is_demo_data,
            }
            for hit in hits[:limit]
        ],
    )


async def get_previous_analysis(analysis_id: str) -> dict[str, Any]:
    """Look up an earlier analysis — the agent's cross-run memory."""
    try:
        parsed = uuid.UUID(analysis_id)
    except ValueError:
        return _error("analysis_id is not a valid UUID.", analysis_id=analysis_id)
    async with session_scope() as session:
        analysis = await repository.get_analysis(session, parsed)
        if analysis is None:
            return _error("Analysis not found.", analysis_id=analysis_id)
        comparables = await repository.get_comparables(session, parsed)
        return _ok(
            analysis={
                "id": str(analysis.id),
                "query": analysis.query,
                "status": analysis.status,
                "assessment": analysis.assessment,
                "confidence": analysis.confidence,
                "asking_price": analysis.asking_price,
                "evidence_low": analysis.evidence_low,
                "evidence_high": analysis.evidence_high,
                "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
            },
            comparable_count=len(comparables),
            included_count=sum(1 for item in comparables if item.included),
        )
