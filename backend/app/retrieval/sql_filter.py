"""STAGE A — deterministic structured filtering in PostgreSQL.

This stage decides *eligibility*: which sold properties a reasonable valuer would
even put on the table. It is intentionally free of any learned or semantic
component, because eligibility is a rules question, not a similarity question:

    a 4-bedroom house is not a comparable for a 2-bedroom apartment,
    no matter how similarly the two listings are written.

Running this first also keeps the expensive stages cheap — embeddings and LLM
grading only ever see a few dozen rows, not the whole corpus.

Everything here is SQL. The funnel counts returned alongside the candidates are
what the UI's progress panel displays, so the user can see the shape of the
search rather than trusting a black box.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Select, and_, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing, Property
from app.schemas.property import PropertyRecord, PropertyType

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class HardFilterCriteria:
    """The eligibility rules, all explicit and all auditable."""

    latitude: float
    longitude: float
    radius_km: float
    lookback_months: int
    property_types: frozenset[PropertyType]
    bedrooms: int | None = None
    bedroom_tolerance: int = 1
    bathrooms: int | None = None
    bathroom_tolerance: int = 1
    floor_area_sqm: float | None = None
    floor_area_tolerance_pct: float = 0.35
    land_area_sqm: float | None = None
    land_area_tolerance_pct: float = 0.50
    exclude_property_ids: frozenset[uuid.UUID] = frozenset()
    limit: int = 120

    @property
    def sold_since(self) -> date:
        return date.today() - timedelta(days=int(self.lookback_months * 30.44))

    def describe(self) -> list[str]:
        rules = [
            f"property type in {{{', '.join(sorted(t.value for t in self.property_types))}}}",
            f"sold within {self.lookback_months} months (since {self.sold_since.isoformat()})",
            f"within {self.radius_km:g} km",
        ]
        if self.bedrooms is not None:
            rules.append(f"bedrooms {self.bedrooms} ± {self.bedroom_tolerance}")
        if self.bathrooms is not None:
            rules.append(f"bathrooms {self.bathrooms} ± {self.bathroom_tolerance}")
        if self.floor_area_sqm is not None:
            rules.append(
                f"floor area within ±{self.floor_area_tolerance_pct:.0%} of "
                f"{self.floor_area_sqm:g} m² (when known)"
            )
        return rules


@dataclass
class CandidateRow:
    """One eligible sold comparable, straight out of Stage A."""

    property_id: uuid.UUID
    external_id: str
    address: str
    suburb: str
    state: str
    postcode: str
    property_type: str
    bedrooms: int | None
    bathrooms: int | None
    carspaces: int | None
    floor_area: float | None
    land_area: float | None
    latitude: float | None
    longitude: float | None
    listing_id: uuid.UUID | None
    sold_price: int
    sold_at: date
    distance_km: float
    description: str | None
    headline: str | None
    source: str
    source_url: str | None
    is_demo_data: bool

    @property
    def months_ago(self) -> float:
        return (date.today() - self.sold_at).days / 30.44


@dataclass
class FunnelCounts:
    """Transparency for the UI: how many rows survived each rule."""

    sold_in_window: int = 0
    after_type: int = 0
    after_recency: int = 0
    after_radius: int = 0
    after_bedrooms: int = 0
    after_size: int = 0

    def to_stage_lines(self) -> list[str]:
        return [
            f"{self.sold_in_window} sold properties in the search window",
            f"→ {self.after_type} of a compatible property type",
            f"→ {self.after_recency} sold recently enough",
            f"→ {self.after_radius} within the search radius",
            f"→ {self.after_bedrooms} with a similar bedroom count",
            f"→ {self.after_size} of comparable size",
        ]


@dataclass
class HardFilterResult:
    candidates: list[CandidateRow] = field(default_factory=list)
    funnel: FunnelCounts = field(default_factory=FunnelCounts)
    criteria_applied: list[str] = field(default_factory=list)


def distance_km_expr(latitude: float, longitude: float) -> Any:
    """Great-circle distance in SQL. Deterministic trigonometry, no extension needed."""
    lat1 = func.radians(literal(latitude))
    lon1 = func.radians(literal(longitude))
    lat2 = func.radians(Property.latitude)
    lon2 = func.radians(Property.longitude)
    haversine = func.pow(func.sin((lat2 - lat1) / 2), 2) + func.cos(lat1) * func.cos(
        lat2
    ) * func.pow(func.sin((lon2 - lon1) / 2), 2)
    return 2 * literal(EARTH_RADIUS_KM) * func.asin(func.sqrt(haversine))


def _bounding_box(latitude: float, longitude: float, radius_km: float) -> Any:
    """Cheap, index-friendly pre-filter applied before the exact trigonometry.

    The box is computed in Python (the centre point is a constant for any given
    query), so PostgreSQL sees plain literal range predicates it can serve from
    `ix_properties_geo`. The exact haversine still runs afterwards, so the box
    only ever removes rows that could not have been within the radius anyway.
    """
    from math import cos, radians

    lat_delta = radius_km / 110.574
    # Longitude degrees shrink towards the poles; clamp guards the degenerate case.
    cos_latitude = max(abs(cos(radians(latitude))), 0.01)
    lon_delta = radius_km / (111.320 * cos_latitude)
    return and_(
        Property.latitude.between(latitude - lat_delta, latitude + lat_delta),
        Property.longitude.between(longitude - lon_delta, longitude + lon_delta),
    )


def _base_query(criteria: HardFilterCriteria) -> Select[Any]:
    """Sold listings joined to properties, restricted to the widest useful window."""
    query = (
        select(
            Property,
            Listing,
            distance_km_expr(criteria.latitude, criteria.longitude).label("distance_km"),
        )
        .join(Listing, Listing.property_id == Property.id)
        .where(
            Listing.status == "sold",
            Listing.sold_price.isnot(None),
            Listing.sold_at.isnot(None),
            Property.latitude.isnot(None),
            Property.longitude.isnot(None),
        )
    )
    if criteria.exclude_property_ids:
        query = query.where(Property.id.notin_(criteria.exclude_property_ids))
    return query


def _type_clause(criteria: HardFilterCriteria) -> Any:
    return Property.property_type.in_([item.value for item in criteria.property_types])


def _recency_clause(criteria: HardFilterCriteria) -> Any:
    return Listing.sold_at >= criteria.sold_since


def _bedroom_clause(criteria: HardFilterCriteria) -> Any:
    if criteria.bedrooms is None:
        return literal(True)
    low = criteria.bedrooms - criteria.bedroom_tolerance
    high = criteria.bedrooms + criteria.bedroom_tolerance
    # Unknown bedroom counts are kept, not silently dropped — an unknown is not a
    # mismatch. The reranker sees the gap and can discount it explicitly.
    return or_(Property.bedrooms.is_(None), Property.bedrooms.between(low, high))


def _bathroom_clause(criteria: HardFilterCriteria) -> Any:
    if criteria.bathrooms is None:
        return literal(True)
    low = criteria.bathrooms - criteria.bathroom_tolerance
    high = criteria.bathrooms + criteria.bathroom_tolerance
    return or_(Property.bathrooms.is_(None), Property.bathrooms.between(low, high))


def _size_clause(criteria: HardFilterCriteria) -> Any:
    clauses = []
    if criteria.floor_area_sqm:
        low = criteria.floor_area_sqm * (1 - criteria.floor_area_tolerance_pct)
        high = criteria.floor_area_sqm * (1 + criteria.floor_area_tolerance_pct)
        clauses.append(or_(Property.floor_area.is_(None), Property.floor_area.between(low, high)))
    if criteria.land_area_sqm:
        low = criteria.land_area_sqm * (1 - criteria.land_area_tolerance_pct)
        high = criteria.land_area_sqm * (1 + criteria.land_area_tolerance_pct)
        clauses.append(or_(Property.land_area.is_(None), Property.land_area.between(low, high)))
    return and_(*clauses) if clauses else literal(True)


async def compute_funnel(session: AsyncSession, criteria: HardFilterCriteria) -> FunnelCounts:
    """Every funnel level in a single pass, using conditional aggregation."""
    distance = distance_km_expr(criteria.latitude, criteria.longitude)
    type_ok = _type_clause(criteria)
    recency_ok = _recency_clause(criteria)
    radius_ok = distance <= criteria.radius_km
    bedrooms_ok = _bedroom_clause(criteria)
    size_ok = and_(_bathroom_clause(criteria), _size_clause(criteria))

    def counted(*conditions: Any) -> Any:
        return func.count(case((and_(*conditions), 1)))

    query = (
        select(
            func.count().label("sold_in_window"),
            counted(type_ok).label("after_type"),
            counted(type_ok, recency_ok).label("after_recency"),
            counted(type_ok, recency_ok, radius_ok).label("after_radius"),
            counted(type_ok, recency_ok, radius_ok, bedrooms_ok).label("after_bedrooms"),
            counted(type_ok, recency_ok, radius_ok, bedrooms_ok, size_ok).label("after_size"),
        )
        .select_from(Property)
        .join(Listing, Listing.property_id == Property.id)
        .where(
            Listing.status == "sold",
            Listing.sold_price.isnot(None),
            Listing.sold_at.isnot(None),
            Property.latitude.isnot(None),
            Property.longitude.isnot(None),
        )
    )
    if criteria.exclude_property_ids:
        query = query.where(Property.id.notin_(criteria.exclude_property_ids))

    row = (await session.execute(query)).one()
    return FunnelCounts(
        sold_in_window=int(row.sold_in_window),
        after_type=int(row.after_type),
        after_recency=int(row.after_recency),
        after_radius=int(row.after_radius),
        after_bedrooms=int(row.after_bedrooms),
        after_size=int(row.after_size),
    )


async def hard_filter(session: AsyncSession, criteria: HardFilterCriteria) -> HardFilterResult:
    """Run Stage A and return eligible candidates plus the funnel that produced them."""
    distance = distance_km_expr(criteria.latitude, criteria.longitude)
    query = (
        _base_query(criteria)
        .where(
            _type_clause(criteria),
            _recency_clause(criteria),
            _bounding_box(criteria.latitude, criteria.longitude, criteria.radius_km),
            distance <= criteria.radius_km,
            _bedroom_clause(criteria),
            _bathroom_clause(criteria),
            _size_clause(criteria),
        )
        # `external_id` is the final tie-break so the candidate order is fully
        # deterministic. Without it, rows with identical distance and sale date can
        # come back in any order, which makes evaluation results drift between runs
        # for no real reason.
        .order_by(distance.asc(), Listing.sold_at.desc(), Property.external_id.asc())
        .limit(criteria.limit)
    )
    rows = (await session.execute(query)).all()
    candidates = [
        CandidateRow(
            property_id=prop.id,
            external_id=prop.external_id,
            address=prop.address,
            suburb=prop.suburb,
            state=prop.state,
            postcode=prop.postcode,
            property_type=prop.property_type,
            bedrooms=prop.bedrooms,
            bathrooms=prop.bathrooms,
            carspaces=prop.carspaces,
            floor_area=prop.floor_area,
            land_area=prop.land_area,
            latitude=prop.latitude,
            longitude=prop.longitude,
            listing_id=listing.id,
            sold_price=int(listing.sold_price or 0),
            sold_at=listing.sold_at,  # type: ignore[arg-type]
            distance_km=round(float(distance_value), 3),
            description=listing.description,
            headline=listing.headline,
            source=listing.source,
            source_url=listing.source_url,
            is_demo_data=listing.is_demo_data,
        )
        for prop, listing, distance_value in rows
    ]
    funnel = await compute_funnel(session, criteria)
    return HardFilterResult(
        candidates=candidates, funnel=funnel, criteria_applied=criteria.describe()
    )


def criteria_for(
    target: PropertyRecord,
    *,
    radius_km: float,
    lookback_months: int,
    exclude_property_ids: frozenset[uuid.UUID] = frozenset(),
    limit: int = 120,
) -> HardFilterCriteria:
    """Derive eligibility rules from the target property's own attributes."""
    if target.coordinates is None:
        raise ValueError("Cannot build hard-filter criteria without target coordinates.")
    return HardFilterCriteria(
        latitude=target.coordinates.latitude,
        longitude=target.coordinates.longitude,
        radius_km=radius_km,
        lookback_months=lookback_months,
        property_types=frozenset(target.property_type.compatible_types),
        bedrooms=target.bedrooms,
        bathrooms=target.bathrooms,
        floor_area_sqm=target.floor_area_sqm,
        land_area_sqm=target.land_area_sqm,
        exclude_property_ids=exclude_property_ids,
        limit=limit,
    )
