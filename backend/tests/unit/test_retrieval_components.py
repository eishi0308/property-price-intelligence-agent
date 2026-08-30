"""Retrieval building blocks: concepts, fusion, structural scoring, metrics."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.retrieval.concepts import CONCEPTS_BY_KEY, detect_concepts, keyword_terms_for
from app.retrieval.embeddings import OfflineConceptEncoder
from app.retrieval.hybrid import DEFAULT_WEIGHTS, structural_score
from app.retrieval.semantic_text import build_semantic_document, clean_listing_text
from app.retrieval.sql_filter import CandidateRow, HardFilterCriteria, criteria_for
from app.schemas.property import (
    Coordinates,
    ListingRecord,
    ListingStatus,
    PropertyRecord,
    PropertyType,
)
from evals.metrics import ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank

pytestmark = pytest.mark.unit


def _record(**overrides) -> PropertyRecord:
    base = {
        "external_id": "T1",
        "provider": "demo",
        "address": "1 Test Street, Strathfield 2135",
        "suburb": "Strathfield",
        "state": "NSW",
        "postcode": "2135",
        "coordinates": Coordinates(latitude=-33.873, longitude=151.095),
        "property_type": PropertyType.APARTMENT,
        "bedrooms": 2,
        "bathrooms": 2,
        "carspaces": 1,
        "floor_area_sqm": 96.0,
    }
    return PropertyRecord(**{**base, **overrides})


def _candidate(**overrides) -> CandidateRow:
    base = {
        "property_id": __import__("uuid").uuid4(),
        "external_id": "C1",
        "address": "2 Test Street",
        "suburb": "Strathfield",
        "state": "NSW",
        "postcode": "2135",
        "property_type": "apartment",
        "bedrooms": 2,
        "bathrooms": 2,
        "carspaces": 1,
        "floor_area": 95.0,
        "land_area": None,
        "latitude": -33.874,
        "longitude": 151.096,
        "listing_id": None,
        "sold_price": 900_000,
        "sold_at": date.today() - timedelta(days=60),
        "distance_km": 0.2,
        "description": None,
        "headline": None,
        "source": "demo",
        "source_url": None,
        "is_demo_data": True,
    }
    return CandidateRow(**{**base, **overrides})


# --- concepts -------------------------------------------------------------
def test_detects_concepts_via_explicit_keywords():
    found = detect_concepts("A renovated north-facing apartment with secure parking.")
    assert found["renovated"] == "keyword"
    assert found["north_facing"] == "keyword"
    assert found["parking"] == "keyword"


def test_detects_concepts_via_paraphrase_with_no_shared_keyword():
    text = "Reimagined throughout, bathed in morning sun, with lock-up garaging."
    found = detect_concepts(text)
    assert found.get("renovated") == "paraphrase"
    assert found.get("north_facing") == "paraphrase"
    assert found.get("parking") == "paraphrase"
    # This is the premise of hybrid retrieval: no keyword overlap at all.
    for term in ("renovated", "north facing", "parking"):
        assert term not in text.lower()


def test_negative_concepts_are_flagged_as_negative():
    found = detect_concepts("Original condition unit fronting a main road.")
    assert CONCEPTS_BY_KEY["original_condition"].direction == "negative"
    assert CONCEPTS_BY_KEY["main_road"].direction == "negative"
    assert set(found) >= {"original_condition", "main_road"}


def test_keyword_terms_are_derived_from_detected_concepts():
    terms = keyword_terms_for(detect_concepts("Renovated with secure parking."))
    assert any("renovat" in term for term in terms)
    assert any("parking" in term or "car" in term for term in terms)


# --- offline encoder ------------------------------------------------------
def test_offline_encoder_is_deterministic_and_normalised():
    encoder = OfflineConceptEncoder(256)
    first = encoder.embed_query("renovated north-facing apartment")
    second = encoder.embed_query("renovated north-facing apartment")
    assert first == second
    assert abs(sum(value * value for value in first) ** 0.5 - 1.0) < 1e-6


def test_offline_encoder_ranks_paraphrase_above_opposite():
    encoder = OfflineConceptEncoder(1536)
    target = encoder.embed_query(
        "Renovated north-facing apartment with oversized balcony and secure parking in a quiet position."
    )

    def cosine(text: str) -> float:
        return sum(a * b for a, b in zip(target, encoder.embed_query(text), strict=True))

    paraphrase = cosine(
        "Recently updated light-filled unit with generous outdoor entertaining space, "
        "secure car accommodation and a peaceful rear aspect."
    )
    opposite = cosine("Original condition ground floor unit on a busy main road with no parking.")
    unrelated = cosine("Industrial warehouse with roller doors and a loading dock.")
    assert paraphrase > opposite > unrelated


def test_offline_encoder_handles_empty_text():
    assert OfflineConceptEncoder(128).embed_query("") == [0.0] * 128


# --- semantic document ----------------------------------------------------
def test_semantic_document_prefers_listing_text():
    listing = ListingRecord(
        external_listing_id="L1",
        property_external_id="T1",
        provider="demo",
        status=ListingStatus.SOLD,
        description="A beautifully renovated north-facing apartment with a large balcony " * 3,
    )
    document = build_semantic_document(_record(), listing)
    assert document.has_real_description
    assert not document.is_degraded
    assert document.source == "listing_description"


def test_semantic_document_degrades_visibly_without_description():
    document = build_semantic_document(_record(), None)
    assert document.is_degraded
    assert document.source == "structured_attributes"
    assert "apartment" in document.text


def test_boilerplate_is_stripped():
    cleaned = clean_listing_text(
        "Lovely home. Disclaimer: all information contained herein is gathered from sources. Nice."
    )
    assert "Disclaimer" not in cleaned
    assert "Lovely home." in cleaned


# --- structural scoring ---------------------------------------------------
def test_structural_score_rewards_closeness():
    near = structural_score(_record(), _candidate(distance_km=0.1), radius_km=3.0)
    far = structural_score(_record(), _candidate(distance_km=2.9), radius_km=3.0)
    assert near > far


def test_structural_score_penalises_bedroom_mismatch():
    same = structural_score(_record(), _candidate(bedrooms=2), radius_km=3.0)
    different = structural_score(_record(), _candidate(bedrooms=4), radius_km=3.0)
    assert same > different


def test_structural_score_is_bounded():
    for candidate in (_candidate(), _candidate(distance_km=3.0, bedrooms=5, bathrooms=5)):
        score = structural_score(_record(), candidate, radius_km=3.0)
        assert 0.0 <= score <= 1.0


# --- hard filter criteria -------------------------------------------------
def test_criteria_only_admit_compatible_property_types():
    criteria = criteria_for(_record(), radius_km=3.0, lookback_months=12)
    assert criteria.property_types == frozenset({PropertyType.APARTMENT})
    assert PropertyType.HOUSE not in criteria.property_types


def test_house_and_apartment_are_never_compatible():
    assert PropertyType.APARTMENT not in PropertyType.HOUSE.compatible_types
    assert PropertyType.HOUSE not in PropertyType.APARTMENT.compatible_types


def test_townhouse_villa_duplex_form_one_cluster():
    assert PropertyType.VILLA in PropertyType.TOWNHOUSE.compatible_types
    assert PropertyType.DUPLEX in PropertyType.TOWNHOUSE.compatible_types


def test_criteria_requires_coordinates():
    with pytest.raises(ValueError, match="coordinates"):
        criteria_for(_record(coordinates=None), radius_km=3.0, lookback_months=12)


def test_criteria_description_lists_every_rule():
    rules = criteria_for(_record(), radius_km=3.0, lookback_months=12).describe()
    joined = " ".join(rules)
    assert "property type" in joined and "km" in joined and "bedrooms" in joined


def test_sold_since_reflects_lookback():
    criteria = HardFilterCriteria(
        latitude=-33.87,
        longitude=151.09,
        radius_km=3.0,
        lookback_months=12,
        property_types=frozenset({PropertyType.APARTMENT}),
    )
    assert (date.today() - criteria.sold_since).days == pytest.approx(365, abs=5)


# --- rank fusion metrics --------------------------------------------------
def test_rrf_weights_default_to_equal():
    assert set(DEFAULT_WEIGHTS.values()) == {1.0}
    assert set(DEFAULT_WEIGHTS) == {"structural", "keyword", "semantic"}


def test_ranking_metrics():
    retrieved = ["a", "x", "b", "y", "c"]
    relevant = {"a", "b", "c", "d"}
    assert precision_at_k(retrieved, relevant, 5) == pytest.approx(0.6)
    assert recall_at_k(retrieved, relevant, 5) == pytest.approx(0.75)
    assert reciprocal_rank(retrieved, relevant) == pytest.approx(1.0)
    assert reciprocal_rank(["x", "y", "a"], relevant) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x", "y"], relevant) == 0.0
    assert 0.0 < ndcg_at_k(retrieved, relevant, 10) <= 1.0


def test_ndcg_is_one_for_a_perfect_ranking():
    assert ndcg_at_k(["a", "b", "c"], {"a", "b", "c"}, 10) == pytest.approx(1.0)


def test_metrics_handle_empty_inputs():
    assert precision_at_k([], {"a"}, 5) == 0.0
    assert recall_at_k(["a"], set(), 5) == 0.0
    assert ndcg_at_k([], set(), 10) == 0.0
