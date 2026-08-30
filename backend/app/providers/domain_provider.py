"""Domain.com.au adapter.

=======================  VERIFICATION STATUS  =======================
Written against Domain's *documented* API v1. No Domain credentials were
available when this was authored, so no response shape has been verified.
Same defensive-normalisation discipline as the PropTrack adapter applies.

Capability note that materially affects this product: Domain's standard
`/v1/salesResults` and property-profile endpoints expose sale prices and
attributes, but full historical *listing description text* is tier-dependent.
`historical_descriptions` is therefore declared False until the feasibility gate
proves otherwise — the honest default, because claiming it would make the
semantic-retrieval stage look stronger than the corpus supports.
=====================================================================
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings, get_settings
from app.providers.base import (
    PropertyDataProvider,
    ProviderAuthError,
    ProviderCapabilities,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from app.providers.proptrack import _coerce_date, _coerce_float, _coerce_int, _coerce_type, _first
from app.schemas.property import (
    Coordinates,
    ListingRecord,
    ListingStatus,
    MarketContext,
    PropertyRecord,
    PropertyResolution,
    PropertyType,
    SoldTransaction,
)

PROVIDER_NAME = "domain"

_PATHS = {
    "address_locators": "/v1/properties/_suggest",
    "property": "/v1/properties/{property_id}",
    "listing": "/v1/listings/{listing_id}",
    "sold_search": "/v1/listings/residential/_search",
    "market_stats": "/v1/locations/profiles",
}


class DomainProvider(PropertyDataProvider):
    """API-key adapter for Domain."""

    capabilities = ProviderCapabilities(
        name=PROVIDER_NAME,
        is_demo=False,
        resolve_by_url=True,  # Domain slugs carry the full address
        resolve_by_address=True,
        property_attributes=True,
        current_listing=True,
        asking_price=True,
        sold_transactions=True,
        historical_listings=True,
        historical_descriptions=False,  # conservative default — prove it, then flip it
        market_context=True,
        notes=(
            "Historical listing description text is plan-dependent. While "
            "historical_descriptions is False the pipeline still runs, but semantic "
            "comparable retrieval falls back to structured-attribute text and the "
            "feasibility report reports VECTOR RETRIEVAL VIABLE = LIMITED."
        ),
    )

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.domain_api_key:
            raise ProviderNotConfiguredError(
                "DOMAIN_API_KEY is required for PROPERTY_PROVIDER=domain."
            )
        self._client = httpx.AsyncClient(
            base_url=self._settings.domain_base_url,
            timeout=self._settings.provider_timeout_seconds,
            headers={"Accept": "application/json", "X-Api-Key": self._settings.domain_api_key},
        )

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type(ProviderUnavailableError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code in (401, 403):
            raise ProviderAuthError("Domain rejected the API key.")
        if response.status_code == 429:
            raise ProviderRateLimitError("Domain rate limit exceeded.")
        if response.status_code == 404:
            return None
        if response.status_code >= 500:
            raise ProviderUnavailableError(f"Domain returned {response.status_code} for {path}.")
        response.raise_for_status()
        return response.json()

    def _normalise_property(self, payload: dict[str, Any]) -> PropertyRecord | None:
        external_id = _first(payload, "id", "propertyId", "listingId")
        if external_id is None:
            return None
        latitude = _coerce_float(
            _first(payload, "addressCoordinate.lat", "geoLocation.latitude", "latitude")
        )
        longitude = _coerce_float(
            _first(payload, "addressCoordinate.lon", "geoLocation.longitude", "longitude")
        )
        coordinates = (
            Coordinates(latitude=latitude, longitude=longitude)
            if latitude is not None and longitude is not None
            else None
        )
        return PropertyRecord(
            external_id=str(external_id),
            provider=PROVIDER_NAME,
            address=str(
                _first(payload, "address", "addressParts.displayAddress", default="Unknown address")
            ),
            suburb=str(_first(payload, "addressParts.suburb", "suburb", default="")),
            state=str(_first(payload, "addressParts.stateAbbreviation", "state", default="")),
            postcode=str(_first(payload, "addressParts.postcode", "postcode", default="")),
            coordinates=coordinates,
            property_type=_coerce_type(_first(payload, "propertyType", "propertyTypes.0")),
            bedrooms=_coerce_int(_first(payload, "bedrooms", "propertyDetails.bedrooms")),
            bathrooms=_coerce_int(_first(payload, "bathrooms", "propertyDetails.bathrooms")),
            carspaces=_coerce_int(_first(payload, "carspaces", "propertyDetails.carspaces")),
            floor_area_sqm=_coerce_float(
                _first(payload, "buildingAreaSqm", "propertyDetails.buildingArea")
            ),
            land_area_sqm=_coerce_float(_first(payload, "landAreaSqm", "propertyDetails.landArea")),
            year_built=_coerce_int(_first(payload, "yearBuilt")),
            is_demo_data=False,
        )

    def _normalise_listing(
        self, payload: dict[str, Any], property_external_id: str
    ) -> ListingRecord | None:
        listing_id = _first(payload, "id", "listingId")
        if listing_id is None:
            return None
        raw_status = str(_first(payload, "status", "saleMode", default="unknown")).lower()
        status = {
            "live": ListingStatus.CURRENT,
            "current": ListingStatus.CURRENT,
            "sold": ListingStatus.SOLD,
            "withdrawn": ListingStatus.WITHDRAWN,
        }.get(raw_status, ListingStatus.UNKNOWN)
        return ListingRecord(
            external_listing_id=str(listing_id),
            property_external_id=property_external_id,
            provider=PROVIDER_NAME,
            status=status,
            asking_price=_coerce_int(_first(payload, "priceDetails.price", "price")),
            price_guide_text=(
                str(_first(payload, "priceDetails.displayPrice", default="")) or None
            ),
            headline=(str(_first(payload, "headline", "advertHeadline", default="")) or None),
            description=(
                str(_first(payload, "description", "advertDescription", default="")) or None
            ),
            listed_at=_coerce_date(_first(payload, "dateListed", "dateAvailable")),
            sold_at=_coerce_date(_first(payload, "soldDetails.soldDate", "dateSold")),
            sold_price=_coerce_int(_first(payload, "soldDetails.soldPrice", "soldPrice")),
            source_url=(str(_first(payload, "seoUrl", "listingUrl", default="")) or None),
            is_demo_data=False,
        )

    async def resolve_property(self, query: str) -> PropertyResolution:
        from app.ingest.url_parser import parse_query

        parsed = parse_query(query)
        if not parsed.is_resolvable:
            return PropertyResolution(
                resolved=False,
                query=query,
                interpreted_as=parsed.describe(),
                failure_reason=parsed.warnings[0] if parsed.warnings else "Unrecognised input.",
            )
        terms = parsed.normalised_address or " ".join(
            part for part in (parsed.suburb, parsed.state, parsed.postcode) if part
        )
        payload = await self._request("GET", _PATHS["address_locators"], params={"terms": terms})
        rows = payload if isinstance(payload, list) else []
        candidates = [
            record
            for record in (self._normalise_property(row) for row in rows if isinstance(row, dict))
            if record is not None
        ]
        if not candidates:
            return PropertyResolution(
                resolved=False,
                query=query,
                interpreted_as=parsed.describe(),
                failure_reason="Domain returned no property suggestion for this query.",
            )
        if len(candidates) > 1 and not parsed.normalised_address:
            return PropertyResolution(
                resolved=False,
                query=query,
                interpreted_as=parsed.describe(),
                candidates=candidates[:10],
                failure_reason="Query was ambiguous; several Domain properties matched.",
            )
        return PropertyResolution(
            resolved=True,
            property_record=candidates[0],
            query=query,
            interpreted_as=parsed.describe(),
        )

    async def get_property(self, external_id: str) -> PropertyRecord | None:
        payload = await self._request("GET", _PATHS["property"].format(property_id=external_id))
        return self._normalise_property(payload) if isinstance(payload, dict) else None

    async def get_current_listing(self, external_id: str) -> ListingRecord | None:
        payload = await self._request("GET", _PATHS["property"].format(property_id=external_id))
        if not isinstance(payload, dict):
            return None
        raw = _first(payload, "activeListing", "currentListing")
        if not isinstance(raw, dict):
            return None
        listing = self._normalise_listing(raw, external_id)
        if listing:
            listing = listing.model_copy(update={"status": ListingStatus.CURRENT})
        return listing

    async def get_historical_listing(self, external_id: str) -> ListingRecord | None:
        payload = await self._request("GET", _PATHS["property"].format(property_id=external_id))
        if not isinstance(payload, dict):
            return None
        history = _first(payload, "salesHistory", "listingHistory", default=[])
        rows = (
            [row for row in history if isinstance(row, dict)] if isinstance(history, list) else []
        )
        listings = [
            listing
            for listing in (self._normalise_listing(row, external_id) for row in rows)
            if listing is not None
        ]
        if not listings:
            return None
        listings.sort(key=lambda item: item.sold_at or date.min, reverse=True)
        return listings[0]

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
        body: dict[str, Any] = {
            "listingType": "Sold",
            "geoWindow": {
                "circle": {
                    "lat": centre_latitude,
                    "lon": centre_longitude,
                    "radiusInMeters": int(radius_km * 1000),
                }
            },
            "soldSince": sold_since.isoformat(),
            "pageSize": min(limit, 200),
        }
        if property_types:
            body["propertyTypes"] = [
                item.value.title() for item in sorted(property_types, key=lambda t: t.value)
            ]
        if min_bedrooms is not None:
            body["minBedrooms"] = min_bedrooms
        if max_bedrooms is not None:
            body["maxBedrooms"] = max_bedrooms

        payload = await self._request("POST", _PATHS["sold_search"], json=body)
        rows = payload if isinstance(payload, list) else []
        centre = Coordinates(latitude=centre_latitude, longitude=centre_longitude)
        transactions: list[SoldTransaction] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            listing_payload = _first(row, "listing", default=row)
            if not isinstance(listing_payload, dict):
                continue
            record = self._normalise_property(
                _first(listing_payload, "propertyDetails", default=listing_payload)
                or listing_payload
            )
            sold_price = _coerce_int(
                _first(listing_payload, "soldData.soldPrice", "priceDetails.price", "soldPrice")
            )
            sold_at = _coerce_date(_first(listing_payload, "soldData.soldDate", "dateSold"))
            if record is None or sold_price is None or sold_at is None:
                continue
            distance = (
                round(centre.distance_km(record.coordinates), 3) if record.coordinates else None
            )
            transactions.append(
                SoldTransaction(
                    property_record=record,
                    listing=self._normalise_listing(listing_payload, record.external_id),
                    sold_price=sold_price,
                    sold_at=sold_at,
                    distance_km=distance,
                    source=PROVIDER_NAME,
                )
            )
        return transactions[:limit]

    async def get_market_context(
        self, *, suburb: str, state: str, postcode: str, property_type: PropertyType | None = None
    ) -> MarketContext | None:
        payload = await self._request(
            "GET",
            _PATHS["market_stats"],
            params={"suburb": suburb, "state": state, "postcode": postcode},
        )
        if not isinstance(payload, dict):
            return None
        return MarketContext(
            suburb=suburb,
            state=state.upper(),
            postcode=postcode,
            property_type=property_type,
            median_sold_price=_coerce_int(_first(payload, "medianSoldPrice", "median")),
            median_price_period=(str(_first(payload, "period", default="")) or None),
            sales_volume_12m=_coerce_int(_first(payload, "numberSold", "salesVolume")),
            median_days_on_market=_coerce_int(_first(payload, "daysOnMarket")),
            price_change_12m_pct=_coerce_float(_first(payload, "annualGrowth")),
            commentary=None,
            source=PROVIDER_NAME,
            as_at=_coerce_date(_first(payload, "asAt")),
            is_demo_data=False,
        )
