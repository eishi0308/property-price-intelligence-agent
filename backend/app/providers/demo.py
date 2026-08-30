"""Fixture-backed provider.

Guarantees the whole product is runnable — end to end, including retrieval,
reranking, RAG and evaluation — with no paid credentials. Every record it
returns carries ``is_demo_data=True``, and that flag is propagated through the
database, the API and the UI. Demo output is never dressed up as live data.
"""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.ingest.url_parser import QueryKind, parse_query
from app.providers.base import PropertyDataProvider, ProviderCapabilities
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

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "demo_dataset.json"
PROVIDER_NAME = "demo"

DEMO_CAPABILITIES = ProviderCapabilities(
    name=PROVIDER_NAME,
    is_demo=True,
    resolve_by_url=True,
    resolve_by_address=True,
    property_attributes=True,
    current_listing=True,
    asking_price=True,
    sold_transactions=True,
    historical_listings=True,
    historical_descriptions=True,
    market_context=True,
    notes="Synthetic fixture corpus. Realistic in shape, fabricated in content.",
)


@lru_cache(maxsize=1)
def load_fixture() -> dict[str, Any]:
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(
            f"Demo fixture missing at {FIXTURE_PATH}. Run: python scripts/generate_fixtures.py"
        )
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _normalise_for_match(text: str) -> str:
    """Aggressive normalisation for fuzzy address comparison."""
    lowered = (text or "").lower()
    lowered = re.sub(r"[,\.]", " ", lowered)
    abbreviations = {
        r"\brd\b": "road",
        r"\bst\b": "street",
        r"\bave\b": "avenue",
        r"\bav\b": "avenue",
        r"\bdr\b": "drive",
        r"\bpl\b": "place",
        r"\bpde\b": "parade",
        r"\bcres\b": "crescent",
        r"\bct\b": "court",
        r"\btce\b": "terrace",
        r"\bcl\b": "close",
        r"\bblvd\b": "boulevarde",
        r"\bhwy\b": "highway",
        r"\bln\b": "lane",
        r"\bunit\b": "",
    }
    for pattern, replacement in abbreviations.items():
        lowered = re.sub(pattern, replacement, lowered)
    return re.sub(r"\s+", " ", lowered).strip()


class DemoProvider(PropertyDataProvider):
    """Serves the deterministic fixture corpus through the production contract."""

    capabilities = DEMO_CAPABILITIES

    def __init__(self, fixture: dict[str, Any] | None = None) -> None:
        self._data = fixture if fixture is not None else load_fixture()
        self._properties: dict[str, dict[str, Any]] = {
            item["external_id"]: item for item in self._data["properties"]
        }
        self._listings_by_property: dict[str, list[dict[str, Any]]] = {}
        for listing in self._data["listings"]:
            self._listings_by_property.setdefault(listing["property_external_id"], []).append(
                listing
            )
        self._alias_index: dict[str, str] = {}
        for target in self._data.get("targets", []):
            for alias in target["aliases"]:
                self._alias_index[_normalise_for_match(alias)] = target["external_id"]
        for external_id, record in self._properties.items():
            self._alias_index[_normalise_for_match(record["address"])] = external_id

    # -- conversion helpers ---------------------------------------------------
    def _to_property(self, raw: dict[str, Any]) -> PropertyRecord:
        coordinates = None
        if raw.get("latitude") is not None and raw.get("longitude") is not None:
            coordinates = Coordinates(latitude=raw["latitude"], longitude=raw["longitude"])
        return PropertyRecord(
            external_id=raw["external_id"],
            provider=PROVIDER_NAME,
            address=raw["address"],
            suburb=raw["suburb"],
            state=raw["state"],
            postcode=raw["postcode"],
            coordinates=coordinates,
            property_type=PropertyType(raw["property_type"]),
            bedrooms=raw.get("bedrooms"),
            bathrooms=raw.get("bathrooms"),
            carspaces=raw.get("carspaces"),
            floor_area_sqm=raw.get("floor_area_sqm"),
            land_area_sqm=raw.get("land_area_sqm"),
            year_built=raw.get("year_built"),
            is_demo_data=True,
        )

    def _to_listing(self, raw: dict[str, Any]) -> ListingRecord:
        return ListingRecord(
            external_listing_id=raw["external_listing_id"],
            property_external_id=raw["property_external_id"],
            provider=PROVIDER_NAME,
            status=ListingStatus(raw["status"]),
            asking_price=raw.get("asking_price"),
            price_guide_text=raw.get("price_guide_text"),
            description=raw.get("description"),
            headline=raw.get("headline"),
            listed_at=date.fromisoformat(raw["listed_at"]) if raw.get("listed_at") else None,
            sold_at=date.fromisoformat(raw["sold_at"]) if raw.get("sold_at") else None,
            sold_price=raw.get("sold_price"),
            source_url=raw.get("source_url"),
            is_demo_data=True,
        )

    # -- contract -------------------------------------------------------------
    async def resolve_property(self, query: str) -> PropertyResolution:
        parsed = parse_query(query)
        if not parsed.is_resolvable:
            return PropertyResolution(
                resolved=False,
                query=query,
                interpreted_as=parsed.describe(),
                failure_reason=parsed.warnings[0] if parsed.warnings else "Unrecognised input.",
            )

        key = _normalise_for_match(parsed.normalised_address or parsed.raw)
        external_id = self._alias_index.get(key)

        if external_id is None:
            # Progressive relaxation: exact -> street-number+street -> suburb shortlist.
            for alias_key, candidate_id in self._alias_index.items():
                if key and (key in alias_key or alias_key in key):
                    external_id = candidate_id
                    break

        if external_id is None and parsed.suburb:
            candidates = [
                self._to_property(raw)
                for raw in self._properties.values()
                if raw["suburb"].lower() == parsed.suburb.lower()
                and any(
                    listing["status"] == "current"
                    for listing in self._listings_by_property.get(raw["external_id"], [])
                )
            ]
            if candidates:
                return PropertyResolution(
                    resolved=False,
                    query=query,
                    interpreted_as=parsed.describe(),
                    candidates=candidates[:10],
                    failure_reason=(
                        f"No exact match in the demo corpus for this address. "
                        f"{len(candidates)} on-market demo propert"
                        f"{'y is' if len(candidates) == 1 else 'ies are'} available in "
                        f"{parsed.suburb}."
                    ),
                )

        if external_id is None:
            hint = ""
            if parsed.kind in {QueryKind.REA_URL, QueryKind.DOMAIN_URL}:
                hint = (
                    " The demo provider cannot look up real listings — configure "
                    "PROPERTY_PROVIDER=proptrack with credentials for live data."
                )
            return PropertyResolution(
                resolved=False,
                query=query,
                interpreted_as=parsed.describe(),
                candidates=[
                    self._to_property(self._properties[target["external_id"]])
                    for target in self._data.get("targets", [])
                ],
                failure_reason=f"Address not present in the demo corpus.{hint}",
            )

        return PropertyResolution(
            resolved=True,
            property_record=self._to_property(self._properties[external_id]),
            query=query,
            interpreted_as=parsed.describe(),
        )

    async def get_property(self, external_id: str) -> PropertyRecord | None:
        raw = self._properties.get(external_id)
        return self._to_property(raw) if raw else None

    async def get_current_listing(self, external_id: str) -> ListingRecord | None:
        for raw in self._listings_by_property.get(external_id, []):
            if raw["status"] == "current":
                return self._to_listing(raw)
        return None

    async def get_historical_listing(self, external_id: str) -> ListingRecord | None:
        sold = [
            raw
            for raw in self._listings_by_property.get(external_id, [])
            if raw["status"] == "sold"
        ]
        if not sold:
            return None
        sold.sort(key=lambda raw: raw["sold_at"] or "", reverse=True)
        return self._to_listing(sold[0])

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
        centre = Coordinates(latitude=centre_latitude, longitude=centre_longitude)
        results: list[SoldTransaction] = []
        for raw_property in self._properties.values():
            if raw_property.get("latitude") is None:
                continue
            property_record = self._to_property(raw_property)
            if property_types and property_record.property_type not in property_types:
                continue
            if min_bedrooms is not None and (property_record.bedrooms or 0) < min_bedrooms:
                continue
            if max_bedrooms is not None and (property_record.bedrooms or 99) > max_bedrooms:
                continue
            assert property_record.coordinates is not None
            distance = centre.distance_km(property_record.coordinates)
            if distance > radius_km:
                continue
            for raw_listing in self._listings_by_property.get(raw_property["external_id"], []):
                if raw_listing["status"] != "sold" or not raw_listing.get("sold_at"):
                    continue
                sold_at = date.fromisoformat(raw_listing["sold_at"])
                if sold_at < sold_since:
                    continue
                results.append(
                    SoldTransaction(
                        property_record=property_record,
                        listing=self._to_listing(raw_listing),
                        sold_price=raw_listing["sold_price"],
                        sold_at=sold_at,
                        distance_km=round(distance, 3),
                        source="demo_fixture",
                    )
                )
        results.sort(key=lambda item: (item.distance_km or 0.0, -item.sold_at.toordinal()))
        return results[:limit]

    async def get_market_context(
        self, *, suburb: str, state: str, postcode: str, property_type: PropertyType | None = None
    ) -> MarketContext | None:
        for raw in self._data.get("market_context", []):
            if raw["suburb"].lower() != suburb.lower() or raw["state"] != state.upper():
                continue
            if property_type and raw.get("property_type") != property_type.value:
                continue
            return MarketContext(
                suburb=raw["suburb"],
                state=raw["state"],
                postcode=raw["postcode"],
                property_type=PropertyType(raw["property_type"])
                if raw.get("property_type")
                else None,
                median_sold_price=raw.get("median_sold_price"),
                median_price_period=raw.get("median_price_period"),
                sales_volume_12m=raw.get("sales_volume_12m"),
                median_days_on_market=raw.get("median_days_on_market"),
                price_change_12m_pct=raw.get("price_change_12m_pct"),
                commentary=raw.get("commentary"),
                source="demo_fixture",
                as_at=date.fromisoformat(raw["as_at"]) if raw.get("as_at") else None,
                is_demo_data=True,
            )
        return None

    # -- demo-only helpers used by seeding and evaluation ---------------------
    def all_properties(self) -> list[PropertyRecord]:
        return [self._to_property(raw) for raw in self._properties.values()]

    def all_listings(self) -> list[ListingRecord]:
        return [self._to_listing(raw) for raw in self._data["listings"]]

    def all_market_context(self) -> list[MarketContext]:
        contexts: list[MarketContext] = []
        for raw in self._data.get("market_context", []):
            contexts.append(
                MarketContext(
                    suburb=raw["suburb"],
                    state=raw["state"],
                    postcode=raw["postcode"],
                    property_type=PropertyType(raw["property_type"]),
                    median_sold_price=raw.get("median_sold_price"),
                    median_price_period=raw.get("median_price_period"),
                    sales_volume_12m=raw.get("sales_volume_12m"),
                    median_days_on_market=raw.get("median_days_on_market"),
                    price_change_12m_pct=raw.get("price_change_12m_pct"),
                    commentary=raw.get("commentary"),
                    source="demo_fixture",
                    as_at=date.fromisoformat(raw["as_at"]) if raw.get("as_at") else None,
                    is_demo_data=True,
                )
            )
        return contexts

    def location_context(self) -> list[dict[str, Any]]:
        return list(self._data.get("location_context", []))

    def concepts_for(self, external_id: str) -> list[str]:
        """Ground-truth qualitative tags. Fixture-only; used by the eval suite."""
        return list(self._properties.get(external_id, {}).get("_concepts", []))

    def target_ids(self) -> list[str]:
        return [target["external_id"] for target in self._data.get("targets", [])]
