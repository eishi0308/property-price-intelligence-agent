"""The provider contract.

Two things make this abstraction load-bearing rather than decorative:

1. **Capability declaration.** Australian property APIs differ enormously in what
   they expose. A provider that cannot return historical listing *descriptions*
   cannot support meaningful semantic comparable retrieval, and the application
   must say so out loud instead of silently producing a weak answer. The
   feasibility gate (`scripts/check_property_data.py`) reads these capabilities
   and verifies them against live responses.

2. **Typed normalisation.** Every adapter returns the canonical
   `app.schemas.property` types, so SQL filtering, embedding, reranking and the
   agent are written once.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from app.schemas.property import (
    ListingRecord,
    MarketContext,
    PropertyRecord,
    PropertyResolution,
    PropertyType,
    SoldTransaction,
)


class ProviderError(RuntimeError):
    """Base class for all provider failures."""


class ProviderNotConfiguredError(ProviderError):
    """Credentials or configuration are missing."""


class ProviderAuthError(ProviderError):
    """Credentials were rejected."""


class ProviderRateLimitError(ProviderError):
    """Provider throttled us."""


class ProviderUnavailableError(ProviderError):
    """Network/5xx failure; the caller may retry or fall back."""


class ProviderCapabilityError(ProviderError):
    """The provider does not support the requested capability."""


@dataclass(frozen=True)
class ProviderCapabilities:
    """What an adapter claims it can do.

    Claims are *verified* by the feasibility script against real responses — a
    claim here is not evidence, it is a hypothesis to test.
    """

    name: str
    is_demo: bool
    resolve_by_url: bool
    resolve_by_address: bool
    property_attributes: bool
    current_listing: bool
    asking_price: bool
    sold_transactions: bool
    historical_listings: bool
    historical_descriptions: bool
    market_context: bool
    notes: str = ""

    @property
    def supports_semantic_retrieval(self) -> bool:
        """Vector retrieval over comparables is only meaningful with text."""
        return self.historical_descriptions


class PropertyDataProvider(ABC):
    """Abstract adapter for an Australian property-data source."""

    capabilities: ProviderCapabilities

    @abstractmethod
    async def resolve_property(self, query: str) -> PropertyResolution:
        """Turn a pasted URL or free-text address into a known property."""

    @abstractmethod
    async def get_property(self, external_id: str) -> PropertyRecord | None:
        """Structured attributes for one property."""

    @abstractmethod
    async def get_current_listing(self, external_id: str) -> ListingRecord | None:
        """The live listing, if the property is on market."""

    @abstractmethod
    async def search_sold_transactions(
        self,
        *,
        centre_latitude: float,
        centre_longitude: float,
        radius_km: float,
        sold_since: date,
        property_types: set[PropertyType] | None = None,
        min_bedrooms: int | None = None,
        max_bedrooms: int | None = None,
        limit: int = 200,
    ) -> list[SoldTransaction]:
        """Recent settled sales near a point.

        Implementations must apply the geographic and recency bounds themselves
        (or as close as the upstream API allows) and set `distance_km`.
        """

    @abstractmethod
    async def get_historical_listing(self, external_id: str) -> ListingRecord | None:
        """The listing as it was marketed at the time of a past sale.

        This is the single most important call for semantic comparability: it is
        where the qualitative description text comes from.
        """

    @abstractmethod
    async def get_market_context(
        self, *, suburb: str, state: str, postcode: str, property_type: PropertyType | None = None
    ) -> MarketContext | None:
        """Suburb-level context used strictly as retrievable evidence."""

    async def close(self) -> None:
        """Release any network resources. Safe to call repeatedly."""
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.capabilities.name} demo={self.capabilities.is_demo}>"
