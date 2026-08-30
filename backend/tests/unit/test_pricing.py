"""Deterministic price evidence, labels and confidence rules."""

from __future__ import annotations

import pytest

from app.assessment.pricing import (
    ComparableSale,
    assess_evidence_quality,
    compute_price_evidence,
    derive_confidence,
    derive_label,
)
from app.schemas.assessment import AssessmentLabel, ConfidenceLevel, EvidenceQuality

pytestmark = pytest.mark.unit


def _comps(prices: list[int]) -> list[ComparableSale]:
    return [
        ComparableSale(
            external_id=f"C{index}",
            address=f"{index} Test Street",
            sold_price=price,
            sold_at="2026-05-01",
            distance_km=1.0,
            months_ago=4.0,
            relevance=0.7,
        )
        for index, price in enumerate(prices)
    ]


def test_uses_interquartile_range_with_five_or_more_comparables():
    evidence = compute_price_evidence(
        _comps([700_000, 850_000, 900_000, 920_000, 1_400_000]), 900_000
    )
    assert evidence.evidence_low > 700_000, "an outlier low sale must not define the floor"
    assert evidence.evidence_high < 1_400_000, "an outlier high sale must not define the ceiling"
    assert evidence.observed_low == 700_000
    assert evidence.observed_high == 1_400_000
    assert "interquartile" in evidence.method


def test_uses_full_span_below_five_comparables_and_says_so():
    evidence = compute_price_evidence(_comps([850_000, 900_000, 950_000]), 900_000)
    assert evidence.evidence_low == 850_000
    assert evidence.evidence_high == 950_000
    assert any("full observed span" in note for note in evidence.notes)


def test_no_comparables_produces_no_range():
    evidence = compute_price_evidence([], 900_000)
    assert not evidence.has_range
    assert evidence.comparables_used == 0


@pytest.mark.parametrize(
    ("asking", "expected"),
    [
        (700_000, AssessmentLabel.UNDERPRICED),
        (880_000, AssessmentLabel.FAIR),
        (950_000, AssessmentLabel.FAIR),
        (1_030_000, AssessmentLabel.SLIGHTLY_HIGH),
        (1_400_000, AssessmentLabel.HIGH),
    ],
)
def test_label_thresholds(asking, expected):
    comps = _comps([850_000, 880_000, 900_000, 920_000, 950_000, 980_000])
    evidence = compute_price_evidence(comps, asking)
    assert derive_label(evidence, EvidenceQuality.STRONG) is expected


def test_insufficient_quality_always_yields_insufficient_evidence():
    evidence = compute_price_evidence(_comps([900_000] * 6), 900_000)
    assert (
        derive_label(evidence, EvidenceQuality.INSUFFICIENT)
        is AssessmentLabel.INSUFFICIENT_EVIDENCE
    )
    assert derive_label(evidence, EvidenceQuality.WEAK) is AssessmentLabel.INSUFFICIENT_EVIDENCE


def test_missing_asking_price_yields_insufficient_evidence():
    evidence = compute_price_evidence(_comps([900_000] * 6), None)
    assert derive_label(evidence, EvidenceQuality.STRONG) is AssessmentLabel.INSUFFICIENT_EVIDENCE


def test_too_few_comparables_is_insufficient_quality():
    quality, reasons = assess_evidence_quality(
        included_count=3,
        dispersion_ratio=0.05,
        median_months_ago=3.0,
        median_distance_km=1.0,
        radius_km=3.0,
        expansions_used=0,
        degraded_embeddings=False,
        degraded_grader=False,
    )
    assert quality is EvidenceQuality.INSUFFICIENT
    assert "at least 4" in reasons[0]


def test_wide_dispersion_demotes_quality():
    strong, _ = assess_evidence_quality(
        included_count=8,
        dispersion_ratio=0.05,
        median_months_ago=3.0,
        median_distance_km=1.0,
        radius_km=3.0,
        expansions_used=0,
        degraded_embeddings=False,
        degraded_grader=False,
    )
    spread, reasons = assess_evidence_quality(
        included_count=8,
        dispersion_ratio=0.45,
        median_months_ago=3.0,
        median_distance_km=1.0,
        radius_km=3.0,
        expansions_used=0,
        degraded_embeddings=False,
        degraded_grader=False,
    )
    assert strong is EvidenceQuality.STRONG
    assert spread is EvidenceQuality.ADEQUATE
    assert any("widely spread" in reason for reason in reasons)


def test_confidence_can_only_fall_never_rise():
    evidence = compute_price_evidence(
        _comps([900_000, 905_000, 910_000, 915_000, 920_000, 925_000]), 910_000
    )
    clean = derive_confidence(
        EvidenceQuality.STRONG,
        evidence,
        expansions_used=0,
        degraded_embeddings=False,
        degraded_grader=False,
    )
    degraded = derive_confidence(
        EvidenceQuality.STRONG,
        evidence,
        expansions_used=2,
        degraded_embeddings=True,
        degraded_grader=True,
    )
    assert clean is ConfidenceLevel.HIGH
    order = [ConfidenceLevel.LOW, ConfidenceLevel.MEDIUM, ConfidenceLevel.HIGH]
    assert order.index(degraded) < order.index(clean)


def test_insufficient_quality_caps_confidence_at_low():
    evidence = compute_price_evidence(_comps([900_000] * 6), 900_000)
    assert (
        derive_confidence(
            EvidenceQuality.INSUFFICIENT,
            evidence,
            expansions_used=0,
            degraded_embeddings=False,
            degraded_grader=False,
        )
        is ConfidenceLevel.LOW
    )
