"""PropTrack adapter.

=======================  VERIFICATION STATUS  =======================
This adapter is written against PropTrack's *documented* REST surface. No
PropTrack credentials were available in the environment where it was authored,
so **no response shape here has been verified against a live API**.

Two consequences are baked into the design rather than papered over:

1. Every field access goes through `_first()` / `_coerce_*`, which tolerate
   missing or renamed keys and return ``None`` instead of inventing a value.
   A missing field becomes a visible "unknown", never a default.

2. `scripts/check_property_data.py` is the acceptance gate. It calls each method
   against the real API and reports which capabilities actually work. Do not
   trust `capabilities` below until that script passes — the flags are a
   hypothesis, and the script is the experiment.

If PropTrack's paths differ from those below, only the `_PATHS` mapping and the
`_normalise_*` helpers need to change. Nothing outside this file knows them.
=====================================================================
"""

from __future__ import annotations

import time
from datetime import date, datetime
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

PROVIDER_NAME = "proptrack"

# Documented paths. UNVERIFIED — see module docstring.
_PATHS = {
    "token": "/oauth2/token",
    "address_match": "/api/v2/address/match",
    "address_suggest": "/api/v2/address/suggest",
    "property_summary": "/api/v2/properties/{property_id}/summary",
    "property_listings": "/api/v2/properties/{property_id}/listings",
    "market_sold": "/api/v2/market/sale/historic",
    "market_summary": "/api/v1/market/sale/houses",
}

_TYPE_MAP = {
    "house": PropertyType.HOUSE,
    "unit": PropertyType.APARTMENT,
    "apartment": PropertyType.APARTMENT,
    "flat": PropertyType.APARTMENT,
    "studio": PropertyType.APARTMENT,
    "townhouse": PropertyType.TOWNHOUSE,
    "villa": PropertyType.VILLA,
    "duplex": PropertyType.DUPLEX,
    "land": PropertyType.LAND,
    "vacantland": PropertyType.LAND,
}


def _first(payload: Any, *paths: str, default: Any = None) -> Any:
    """Read the first present value among dotted paths.

    Defensive by construction: an absent path yields ``default`` (normally
    ``None``) so a renamed upstream field surfaces as an explicit unknown rather
    than a silently wrong number.
    """
    for path in paths:
        cursor: Any = payload
        for key in path.split("."):
            if isinstance(cursor, dict) and key in cursor:
                cursor = cursor[key]
            elif isinstance(cursor, list) and key.isdigit() and int(key) < len(cursor):
                cursor = cursor[int(key)]
            else:
                cursor = None
                break
        if cursor is not None:
            return cursor
    return default


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].rstrip("Z"), fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _coerce_type(value: Any) -> PropertyType:
    if not value:
        return PropertyType.OTHER
    key = str(value).strip().lower().replace(" ", "").replace("-", "")
    return _TYPE_MAP.get(key, PropertyType.OTHER)


class PropTrackProvider(PropertyDataProvider):
    """OAuth2 client-credentials adapter for PropTrack."""

    capabilities = ProviderCapabilities(
        name=PROVIDER_NAME,
        is_demo=False,
        resolve_by_url=False,  # URL slugs are parsed locally, then matched by address
        resolve_by_address=True,
        property_attributes=True,
        current_listing=True,
        asking_price=True,
        sold_transactions=True,
        historical_listings=True,
        historical_descriptions=True,  # HYPOTHESIS — plan-dependent; verify with the gate
        market_context=True,
        notes=(
            "Response shapes are unverified in this environment. Historical listing "
            "descriptions are subscription-tier dependent; if absent, semantic comparable "
            "retrieval degrades and the feasibility report must say so."
        ),
    )

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not (self._settings.proptrack_client_id and self._settings.proptrack_client_secret):
            raise ProviderNotConfiguredError(
                "PROPTRACK_CLIENT_ID and PROPTRACK_CLIENT_SECRET are required for "
                "PROPERTY_PROVIDER=proptrack."
            )
        self._client = httpx.AsyncClient(
            base_url=self._settings.proptrack_base_url,
            timeout=self._settings.provider_timeout_seconds,
            headers={"Accept": "application/json"},
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    # -- transport -------------------------------------------------------------
    async def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 30:
            return self._token
        response = await self._client.post(
            _PATHS["token"],
            data={"grant_type": "client_credentials"},
            auth=(
                self._settings.proptrack_client_id or "",
                self._settings.proptrack_client_secret or "",
            ),
        )
        if response.status_code in (401, 403):
            raise ProviderAuthError("PropTrack rejected the client credentials.")
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"PropTrack token endpoint returned {response.status_code}."
            )
        response.raise_for_status()
        payload = response.json()
        token = _first(payload, "access_token", "accessToken")
        if not token:
            raise ProviderAuthError("PropTrack token response contained no access_token.")
        self._token = str(token)
        self._token_expires_at = time.time() + float(
            _first(payload, "expires_in", default=3600) or 3600
        )
        return self._token

    @retry(
        retry=retry_if_exception_type(ProviderUnavailableError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        token = await self._ensure_token()
        response = await self._client.get(
            path, params=params, headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code == 401:
            self._token = None
            raise ProviderAuthError("PropTrack rejected the bearer token.")
        if response.status_code == 429:
            raise ProviderRateLimitError("PropTrack rate limit exceeded.")
        if response.status_code == 404:
            return None
        if response.status_code >= 500:
            raise ProviderUnavailableError(f"PropTrack returned {response.status_code} for {path}.")
        response.raise_for_status()
        return response.json()

    # -- normalisation ---------------------------------------------------------
    def _normalise_property(self, payload: dict[str, Any]) -> PropertyRecord | None:
        external_id = _first(payload, "propertyId", "property_id", "id", "attributes.propertyId")
        if external_id is None:
            return None
        latitude = _coerce_float(
            _first(payload, "address.location.latitude", "location.latitude", "latitude")
        )
        longitude = _coerce_float(
            _first(payload, "address.location.longitude", "location.longitude", "longitude")
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
                _first(
                    payload,
                    "address.fullAddress",
                    "address.full",
                    "fullAddress",
                    default="Unknown address",
                )
            ),
            suburb=str(_first(payload, "address.suburb", "suburb", default="")),
            state=str(_first(payload, "address.state", "state", default="")),
            postcode=str(_first(payload, "address.postcode", "postcode", default="")),
            coordinates=coordinates,
            property_type=_coerce_type(
                _first(payload, "attributes.propertyType", "propertyType", "type")
            ),
            bedrooms=_coerce_int(_first(payload, "attributes.bedrooms", "bedrooms")),
            bathrooms=_coerce_int(_first(payload, "attributes.bathrooms", "bathrooms")),
            carspaces=_coerce_int(
                _first(payload, "attributes.carSpaces", "attributes.carspaces", "carSpaces")
            ),
            floor_area_sqm=_coerce_float(
                _first(payload, "attributes.floorArea", "attributes.buildingArea", "floorArea")
            ),
            land_area_sqm=_coerce_float(_first(payload, "attributes.landArea", "landArea")),
            year_built=_coerce_int(_first(payload, "attributes.yearBuilt", "yearBuilt")),
            is_demo_data=False,
        )

    def _normalise_listing(
        self, payload: dict[str, Any], property_external_id: str
    ) -> ListingRecord | None:
        listing_id = _first(payload, "listingId", "listing_id", "id")
        if listing_id is None:
            return None
        raw_status = str(_first(payload, "status", "listingStatus", default="unknown")).lower()
        status = {
            "current": ListingStatus.CURRENT,
            "active": ListingStatus.CURRENT,
            "sold": ListingStatus.SOLD,
            "withdrawn": ListingStatus.WITHDRAWN,
            "offmarket": ListingStatus.OFF_MARKET,
        }.get(raw_status.replace(" ", "").replace("_", ""), ListingStatus.UNKNOWN)
        return ListingRecord(
            external_listing_id=str(listing_id),
            property_external_id=property_external_id,
            provider=PROVIDER_NAME,
            status=status,
            asking_price=_coerce_int(_first(payload, "priceValue", "price.value", "listPrice")),
            price_guide_text=(
                str(_first(payload, "priceDescription", "price.display", default="")) or None
            ),
            headline=(str(_first(payload, "title", "headline", default="")) or None),
            description=(
                str(_first(payload, "description", "listingDescription", default="")) or None
            ),
            listed_at=_coerce_date(_first(payload, "listedDate", "dateListed", "listingDate")),
            sold_at=_coerce_date(_first(payload, "soldDate", "dateSold", "saleDate")),
            sold_price=_coerce_int(_first(payload, "soldPrice", "salePrice", "price.sold")),
            source_url=(str(_first(payload, "listingUrl", "url", default="")) or None),
            is_demo_data=False,
        )

    # -- contract --------------------------------------------------------------
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
        search_text = parsed.normalised_address or " ".join(
            part for part in (parsed.suburb, parsed.state, parsed.postcode) if part
        )
        if not search_text:
            return PropertyResolution(
                resolved=False,
                query=query,
                interpreted_as=parsed.describe(),
                failure_reason=(
                    "The URL slug did not contain a street address. PropTrack resolves by "
                    "address, so paste the property's street address instead."
                ),
            )
        payload = await self._get(_PATHS["address_match"], params={"q": search_text})
        record = None
        if isinstance(payload, dict):
            record = self._normalise_property(payload)
        elif isinstance(payload, list) and payload:
            record = self._normalise_property(payload[0])
        if record is None:
            return PropertyResolution(
                resolved=False,
                query=query,
                interpreted_as=parsed.describe(),
                failure_reason="PropTrack returned no address match for this query.",
            )
        return PropertyResolution(
            resolved=True, property_record=record, query=query, interpreted_as=parsed.describe()
        )

    async def get_property(self, external_id: str) -> PropertyRecord | None:
        payload = await self._get(_PATHS["property_summary"].format(property_id=external_id))
        return self._normalise_property(payload) if isinstance(payload, dict) else None

    async def _listings(self, external_id: str) -> list[dict[str, Any]]:
        payload = await self._get(_PATHS["property_listings"].format(property_id=external_id))
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            items = _first(payload, "listings", "data", "results", default=[])
            return (
                [item for item in items if isinstance(item, dict)]
                if isinstance(items, list)
                else []
            )
        return []

    async def get_current_listing(self, external_id: str) -> ListingRecord | None:
        for raw in await self._listings(external_id):
            listing = self._normalise_listing(raw, external_id)
            if listing and listing.status is ListingStatus.CURRENT:
                return listing
        return None

    async def get_historical_listing(self, external_id: str) -> ListingRecord | None:
        sold: list[ListingRecord] = []
        for raw in await self._listings(external_id):
            listing = self._normalise_listing(raw, external_id)
            if listing and listing.status is ListingStatus.SOLD:
                sold.append(listing)
        if not sold:
            return None
        sold.sort(key=lambda item: item.sold_at or date.min, reverse=True)
        return sold[0]

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
        params: dict[str, Any] = {
            "latitude": centre_latitude,
            "longitude": centre_longitude,
            "radius": radius_km,
            "fromDate": sold_since.isoformat(),
            "pageSize": min(limit, 200),
        }
        if property_types:
            params["propertyTypes"] = ",".join(sorted(item.value for item in property_types))
        if min_bedrooms is not None:
            params["minBedrooms"] = min_bedrooms
        if max_bedrooms is not None:
            params["maxBedrooms"] = max_bedrooms

        payload = await self._get(_PATHS["market_sold"], params=params)
        rows = (
            payload
            if isinstance(payload, list)
            else _first(payload or {}, "results", "data", default=[])
        )
        centre = Coordinates(latitude=centre_latitude, longitude=centre_longitude)
        transactions: list[SoldTransaction] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            record = self._normalise_property(row)
            sold_price = _coerce_int(_first(row, "soldPrice", "salePrice", "price"))
            sold_at = _coerce_date(_first(row, "soldDate", "saleDate", "dateSold"))
            if record is None or sold_price is None or sold_at is None:
                # Incomplete rows are dropped, never back-filled with guesses.
                continue
            distance = (
                round(centre.distance_km(record.coordinates), 3) if record.coordinates else None
            )
            listing = None
            raw_listing = _first(row, "listing", "listingDetails")
            if isinstance(raw_listing, dict):
                listing = self._normalise_listing(raw_listing, record.external_id)
            transactions.append(
                SoldTransaction(
                    property_record=record,
                    listing=listing,
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
        payload = await self._get(
            _PATHS["market_summary"],
            params={
                "suburb": suburb,
                "state": state,
                "postcode": postcode,
                "propertyTypes": property_type.value if property_type else None,
            },
        )
        if not isinstance(payload, dict):
            return None
        return MarketContext(
            suburb=suburb,
            state=state.upper(),
            postcode=postcode,
            property_type=property_type,
            median_sold_price=_coerce_int(_first(payload, "medianSalePrice", "median.value")),
            median_price_period=(str(_first(payload, "period", default="")) or None),
            sales_volume_12m=_coerce_int(_first(payload, "salesVolume", "transactionCount")),
            median_days_on_market=_coerce_int(
                _first(payload, "medianDaysOnMarket", "daysOnMarket")
            ),
            price_change_12m_pct=_coerce_float(_first(payload, "growth12Months", "annualGrowth")),
            commentary=(str(_first(payload, "commentary", default="")) or None),
            source=PROVIDER_NAME,
            as_at=_coerce_date(_first(payload, "asAt", "date")),
            is_demo_data=False,
        )
