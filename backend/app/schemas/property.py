"""Canonical property domain model.

Every provider adapter normalises its own payload into these types, so nothing
downstream (SQL filtering, retrieval, the agent, the API) is coupled to a
particular data vendor's field names.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from math import asin, cos, radians, sin, sqrt

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PropertyType(str, Enum):
    HOUSE = "house"
    APARTMENT = "apartment"
    TOWNHOUSE = "townhouse"
    VILLA = "villa"
    DUPLEX = "duplex"
    LAND = "land"
    OTHER = "other"

    @property
    def is_strata_style(self) -> bool:
        """Attached dwellings that compete with each other in a buyer's mind."""
        return self in {PropertyType.APARTMENT, PropertyType.VILLA}

    @property
    def compatible_types(self) -> set[PropertyType]:
        """Types a buyer would realistically treat as substitutes.

        Deliberately conservative: a house is never a comparable for an
        apartment. Townhouse/villa/duplex form one attached-but-titled cluster.
        """
        clusters: list[set[PropertyType]] = [
            {PropertyType.APARTMENT},
            {PropertyType.HOUSE},
            {PropertyType.TOWNHOUSE, PropertyType.VILLA, PropertyType.DUPLEX},
            {PropertyType.LAND},
        ]
        for cluster in clusters:
            if self in cluster:
                return set(cluster)
        return {self}


class ListingStatus(str, Enum):
    CURRENT = "current"
    SOLD = "sold"
    WITHDRAWN = "withdrawn"
    OFF_MARKET = "off_market"
    UNKNOWN = "unknown"


class Coordinates(BaseModel):
    model_config = ConfigDict(frozen=True)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    def distance_km(self, other: Coordinates) -> float:
        """Great-circle distance. Deterministic arithmetic, not a model."""
        earth_radius_km = 6371.0088
        lat1, lon1, lat2, lon2 = map(
            radians, (self.latitude, self.longitude, other.latitude, other.longitude)
        )
        dlat, dlon = lat2 - lat1, lon2 - lon1
        h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        return 2 * earth_radius_km * asin(sqrt(h))


class PropertyRecord(BaseModel):
    """Structured attributes of a single dwelling."""

    external_id: str
    provider: str
    address: str
    suburb: str
    state: str
    postcode: str
    coordinates: Coordinates | None = None
    property_type: PropertyType = PropertyType.OTHER
    bedrooms: int | None = Field(default=None, ge=0, le=20)
    bathrooms: int | None = Field(default=None, ge=0, le=20)
    carspaces: int | None = Field(default=None, ge=0, le=20)
    floor_area_sqm: float | None = Field(default=None, gt=0, le=10_000)
    land_area_sqm: float | None = Field(default=None, gt=0, le=1_000_000)
    year_built: int | None = None
    is_demo_data: bool = False

    @field_validator("state")
    @classmethod
    def _normalise_state(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def short_address(self) -> str:
        return self.address.split(",")[0].strip()

    def known_attribute_count(self) -> int:
        """How much structured detail we actually have. Feeds confidence rules."""
        fields = (
            self.bedrooms,
            self.bathrooms,
            self.carspaces,
            self.floor_area_sqm,
            self.land_area_sqm,
            self.coordinates,
        )
        return sum(1 for field in fields if field is not None)


class ListingRecord(BaseModel):
    """A marketing listing (current or historical) attached to a property."""

    external_listing_id: str
    property_external_id: str
    provider: str
    status: ListingStatus = ListingStatus.UNKNOWN
    asking_price: int | None = Field(default=None, ge=0)
    price_guide_text: str | None = None
    description: str | None = None
    headline: str | None = None
    listed_at: date | None = None
    sold_at: date | None = None
    sold_price: int | None = Field(default=None, ge=0)
    source_url: str | None = None
    is_demo_data: bool = False

    @property
    def has_qualitative_text(self) -> bool:
        return bool(self.description and len(self.description.strip()) >= 80)


class SoldTransaction(BaseModel):
    """A settled sale, joined to whatever property/listing detail is available."""

    property_record: PropertyRecord
    listing: ListingRecord | None = None
    sold_price: int = Field(ge=0)
    sold_at: date
    distance_km: float | None = None
    source: str = "unknown"

    @property
    def external_id(self) -> str:
        return self.property_record.external_id

    @property
    def description(self) -> str | None:
        return self.listing.description if self.listing else None


class MarketContext(BaseModel):
    """Suburb-level context used as *evidence*, never as a price prediction."""

    suburb: str
    state: str
    postcode: str
    property_type: PropertyType | None = None
    median_sold_price: int | None = None
    median_price_period: str | None = None
    sales_volume_12m: int | None = None
    median_days_on_market: int | None = None
    price_change_12m_pct: float | None = None
    commentary: str | None = None
    source: str = "unknown"
    as_at: date | None = None
    is_demo_data: bool = False


class PropertyResolution(BaseModel):
    """Result of turning a pasted URL / address into a known property."""

    resolved: bool
    property_record: PropertyRecord | None = None
    query: str
    interpreted_as: str
    candidates: list[PropertyRecord] = Field(default_factory=list)
    failure_reason: str | None = None
