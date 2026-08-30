"""Provider adapters, PostgreSQL persistence and the Stage-A SQL filter."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.db import repository
from app.db.engine import session_scope
from app.providers import build_provider
from app.providers.base import ProviderNotConfiguredError
from app.providers.demo import DemoProvider
from app.providers.domain_provider import DomainProvider
from app.providers.proptrack import PropTrackProvider, _coerce_date, _coerce_int, _first
from app.retrieval.sql_filter import criteria_for, hard_filter
from app.schemas.property import PropertyType
from tests.conftest import TARGET_QUERY, TARGET_URL

pytestmark = pytest.mark.integration


# --- provider contract ----------------------------------------------------
async def test_demo_provider_resolves_url_and_address_to_the_same_property(
    demo_provider: DemoProvider,
):
    by_url = await demo_provider.resolve_property(TARGET_URL)
    by_address = await demo_provider.resolve_property(TARGET_QUERY)
    assert by_url.resolved and by_address.resolved
    assert by_url.property_record.external_id == by_address.property_record.external_id


async def test_demo_provider_flags_all_records_as_demo(demo_provider: DemoProvider):
    resolution = await demo_provider.resolve_property(TARGET_QUERY)
    record = resolution.property_record
    assert record.is_demo_data is True
    listing = await demo_provider.get_current_listing(record.external_id)
    assert listing.is_demo_data is True
    context = await demo_provider.get_market_context(
        suburb=record.suburb, state=record.state, postcode=record.postcode
    )
    assert context.is_demo_data is True


async def test_unresolvable_address_returns_reason_not_exception(demo_provider: DemoProvider):
    resolution = await demo_provider.resolve_property("99 Nowhere Street, Strathfield NSW 2135")
    assert resolution.resolved is False
    assert resolution.failure_reason


async def test_sold_search_respects_radius_and_recency(
    demo_provider: DemoProvider, target_property
):
    coordinates = target_property.coordinates
    sold = await demo_provider.search_sold_transactions(
        centre_latitude=coordinates.latitude,
        centre_longitude=coordinates.longitude,
        radius_km=1.5,
        sold_since=date.today() - timedelta(days=365),
        limit=500,
    )
    assert sold
    assert all(item.distance_km <= 1.5 for item in sold)
    assert all(item.sold_at >= date.today() - timedelta(days=365) for item in sold)


async def test_sold_search_type_filter(demo_provider: DemoProvider, target_property):
    coordinates = target_property.coordinates
    houses = await demo_provider.search_sold_transactions(
        centre_latitude=coordinates.latitude,
        centre_longitude=coordinates.longitude,
        radius_km=5.0,
        sold_since=date.today() - timedelta(days=730),
        property_types={PropertyType.HOUSE},
        limit=500,
    )
    assert houses
    assert all(item.property_record.property_type is PropertyType.HOUSE for item in houses)


def test_live_providers_refuse_to_start_without_credentials():
    """A misconfigured live provider must fail loudly, never fall back to fixtures."""
    with pytest.raises(ProviderNotConfiguredError):
        PropTrackProvider()
    with pytest.raises(ProviderNotConfiguredError):
        DomainProvider()


def test_defensive_field_access_returns_none_for_missing_keys():
    payload = {"address": {"suburb": "Strathfield"}, "attributes": {"bedrooms": 2}}
    assert _first(payload, "address.suburb") == "Strathfield"
    assert _first(payload, "attributes.bedrooms") == 2
    assert _first(payload, "attributes.renamedField") is None
    assert _first(payload, "totally.absent.path", "address.suburb") == "Strathfield"
    assert _coerce_int(None) is None
    assert _coerce_int("not a number") is None
    assert _coerce_int("3") == 3
    assert _coerce_date("2026-05-01") == date(2026, 5, 1)
    assert _coerce_date("nonsense") is None


def test_provider_factory_returns_configured_provider():
    provider = build_provider()
    assert provider.capabilities.name == "demo"
    assert provider.capabilities.is_demo is True


def test_capability_declaration_gates_semantic_retrieval():
    assert DemoProvider.capabilities.supports_semantic_retrieval is True
    # Domain conservatively declares no historical descriptions until proven.
    assert DomainProvider.capabilities.historical_descriptions is False
    assert DomainProvider.capabilities.supports_semantic_retrieval is False


# --- persistence + SQL filter ---------------------------------------------
async def test_corpus_is_seeded(database_ready):
    async with session_scope() as session:
        assert await repository.count_properties(session) > 100
        assert await repository.count_embedded_chunks(session) > 100


async def test_hard_filter_narrows_and_reports_a_funnel(database_ready, target_property):
    async with session_scope() as session:
        row = await repository.get_property_by_external_id(
            session, "demo", target_property.external_id
        )
        criteria = criteria_for(
            target_property,
            radius_km=3.0,
            lookback_months=12,
            exclude_property_ids=frozenset({row.id}),
        )
        result = await hard_filter(session, criteria)

    assert result.candidates
    funnel = result.funnel
    assert funnel.sold_in_window >= funnel.after_type >= funnel.after_recency >= funnel.after_radius
    assert funnel.after_radius >= funnel.after_bedrooms >= funnel.after_size
    assert len(result.candidates) <= funnel.after_size


async def test_hard_filter_excludes_incompatible_types_and_the_target_itself(
    database_ready, target_property
):
    async with session_scope() as session:
        row = await repository.get_property_by_external_id(
            session, "demo", target_property.external_id
        )
        result = await hard_filter(
            session,
            criteria_for(
                target_property,
                radius_km=3.0,
                lookback_months=12,
                exclude_property_ids=frozenset({row.id}),
            ),
        )
    assert all(item.property_type == "apartment" for item in result.candidates)
    assert all(item.property_id != row.id for item in result.candidates)


async def test_hard_filter_respects_the_radius(database_ready, target_property):
    async with session_scope() as session:
        narrow = await hard_filter(
            session, criteria_for(target_property, radius_km=1.0, lookback_months=12)
        )
        wide = await hard_filter(
            session, criteria_for(target_property, radius_km=5.0, lookback_months=12)
        )
    assert all(item.distance_km <= 1.0 + 1e-6 for item in narrow.candidates)
    assert len(wide.candidates) >= len(narrow.candidates)


async def test_upsert_is_idempotent(database_ready, target_property):
    async with session_scope() as session:
        before = await repository.count_properties(session)
        first = await repository.upsert_property(session, target_property)
        second = await repository.upsert_property(session, target_property)
        after = await repository.count_properties(session)
    assert first == second
    assert before == after
