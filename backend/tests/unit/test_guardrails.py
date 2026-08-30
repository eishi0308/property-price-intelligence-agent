"""Guardrails — the checks that stand between a model and a six-figure decision."""

from __future__ import annotations

import pytest

from app.guardrails import DISCLAIMER, apply_guardrails, check_prohibited_claims
from app.schemas.assessment import (
    AssessmentLabel,
    ComparableCitation,
    ConfidenceLevel,
    EvidenceQuality,
    PriceAssessment,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("This property is worth exactly $893,721.", "point valuation"),
        ("The property will appreciate by 8% next year.", "appreciation forecast"),
        ("This is a professional valuation of the property.", "professional valuation claim"),
        ("You should offer $920,000 for this home.", "financial advice"),
        ("Guaranteed value of at least $900,000.", "guaranteed value"),
        ("The rental yield of 4.2% makes this attractive.", "investment return claim"),
        ("The true market value is $1,000,000.", "market value claim"),
    ],
)
def test_prohibited_claims_are_detected(text, rule):
    findings = check_prohibited_claims(text)
    assert findings, f"expected {rule!r} to be caught"
    assert findings[0][0] == rule


@pytest.mark.parametrize(
    "text",
    [
        "Six comparable sales were found within 2.3 km and five sold between $870k and $910k.",
        "Available evidence does not fully explain the $950,000 asking price.",
        "The target has parking and renovated interiors that support some premium.",
        "Its floor area is smaller than two of the highest-priced comparables.",
    ],
)
def test_legitimate_evidence_statements_are_allowed(text):
    assert check_prohibited_claims(text) == []


def _assessment(**overrides) -> PriceAssessment:
    base = {
        "assessment": AssessmentLabel.FAIR,
        "asking_price": 950_000,
        "evidence_range_low": 880_000,
        "evidence_range_high": 960_000,
        "evidence_median": 915_000,
        "confidence": ConfidenceLevel.HIGH,
        "reasoning_summary": "Comparables sold between $880,000 and $960,000 [E1].",
        "supporting_comparables": [
            ComparableCitation(
                external_id="C1",
                address="1 Real Street",
                sold_price=900_000,
                sold_at="2026-05-01",
                distance_km=0.5,
                relevance=0.8,
            )
        ],
        "citations": ["E1"],
        "evidence_quality": EvidenceQuality.STRONG,
    }
    return PriceAssessment(**{**base, **overrides})


_ALLOWED = {
    "C1": ComparableCitation(
        external_id="C1",
        address="1 Real Street",
        sold_price=900_000,
        sold_at="2026-05-01",
        distance_km=0.5,
        relevance=0.8,
    )
}
_EVIDENCE_TEXT = (
    "[E1] 1 Real Street sold for $900,000. Range $880,000 to $960,000, median $915,000."
)


def test_clean_assessment_passes_untouched():
    cleaned, report = apply_guardrails(
        _assessment(),
        allowed_citation_ids={"E1"},
        allowed_comparables=_ALLOWED,
        evidence_text=_EVIDENCE_TEXT,
        evidence_quality=EvidenceQuality.STRONG,
    )
    assert report.passed_clean
    assert cleaned.confidence is ConfidenceLevel.HIGH
    assert len(cleaned.supporting_comparables) == 1


def test_invented_comparable_is_removed():
    assessment = _assessment(
        supporting_comparables=[
            _ALLOWED["C1"],
            ComparableCitation(
                external_id="GHOST",
                address="99 Imaginary Lane",
                sold_price=1_500_000,
                sold_at="2026-01-01",
                distance_km=0.1,
                relevance=0.99,
            ),
        ]
    )
    cleaned, report = apply_guardrails(
        assessment,
        allowed_citation_ids={"E1"},
        allowed_comparables=_ALLOWED,
        evidence_text=_EVIDENCE_TEXT,
        evidence_quality=EvidenceQuality.STRONG,
    )
    assert [item.external_id for item in cleaned.supporting_comparables] == ["C1"]
    assert any("invented comparable" in claim for claim in report.blocked_claims)


def test_citation_to_unretrieved_evidence_is_stripped():
    cleaned, report = apply_guardrails(
        _assessment(citations=["E1", "E9"], reasoning_summary="Supported by [E9]."),
        allowed_citation_ids={"E1"},
        allowed_comparables=_ALLOWED,
        evidence_text=_EVIDENCE_TEXT,
        evidence_quality=EvidenceQuality.STRONG,
    )
    assert cleaned.citations == ["E1"]
    assert "E9" in report.removed_citations
    assert "E9" not in cleaned.reasoning_summary


def test_prohibited_sentence_is_removed_from_the_summary():
    assessment = _assessment(
        reasoning_summary=(
            "Comparables sold between $880,000 and $960,000. "
            "This property is worth exactly $915,000. "
            "The evidence supports the asking price."
        )
    )
    cleaned, report = apply_guardrails(
        assessment,
        allowed_citation_ids={"E1"},
        allowed_comparables=_ALLOWED,
        evidence_text=_EVIDENCE_TEXT,
        evidence_quality=EvidenceQuality.STRONG,
    )
    assert "worth exactly" not in cleaned.reasoning_summary
    assert "The evidence supports the asking price." in cleaned.reasoning_summary
    assert report.blocked_claims


def test_unsupported_dollar_figure_is_flagged():
    cleaned, report = apply_guardrails(
        _assessment(reasoning_summary="Similar homes sell for $2,750,000 in this street."),
        allowed_citation_ids={"E1"},
        allowed_comparables=_ALLOWED,
        evidence_text=_EVIDENCE_TEXT,
        evidence_quality=EvidenceQuality.STRONG,
    )
    assert report.unsupported_figures
    assert any("could not be traced" in unknown for unknown in cleaned.unknowns)


def test_derived_figures_are_accepted_as_supported():
    # 950,000 - 960,000 = -10,000: our own arithmetic, not an invention.
    _cleaned, report = apply_guardrails(
        _assessment(reasoning_summary="The asking price sits $10,000 below the top of the range."),
        allowed_citation_ids={"E1"},
        allowed_comparables=_ALLOWED,
        evidence_text=_EVIDENCE_TEXT,
        evidence_quality=EvidenceQuality.STRONG,
        derived_figures={-10_000},
    )
    assert not report.unsupported_figures


def test_confidence_is_capped_by_evidence_quality():
    cleaned, report = apply_guardrails(
        _assessment(confidence=ConfidenceLevel.HIGH),
        allowed_citation_ids={"E1"},
        allowed_comparables=_ALLOWED,
        evidence_text=_EVIDENCE_TEXT,
        evidence_quality=EvidenceQuality.WEAK,
    )
    assert cleaned.confidence is ConfidenceLevel.LOW
    assert report.confidence_downgraded


def test_insufficient_evidence_forces_low_confidence():
    cleaned, _report = apply_guardrails(
        _assessment(
            assessment=AssessmentLabel.INSUFFICIENT_EVIDENCE, confidence=ConfidenceLevel.HIGH
        ),
        allowed_citation_ids={"E1"},
        allowed_comparables=_ALLOWED,
        evidence_text=_EVIDENCE_TEXT,
        evidence_quality=EvidenceQuality.STRONG,
    )
    assert cleaned.confidence is ConfidenceLevel.LOW


def test_losing_every_comparable_downgrades_the_assessment():
    cleaned, _report = apply_guardrails(
        _assessment(),
        allowed_citation_ids={"E1"},
        allowed_comparables={},
        evidence_text=_EVIDENCE_TEXT,
        evidence_quality=EvidenceQuality.STRONG,
    )
    assert cleaned.assessment is AssessmentLabel.INSUFFICIENT_EVIDENCE
    assert cleaned.supporting_comparables == []


def test_disclaimer_wording_is_exact():
    assert "not a professional property valuation or financial advice" in DISCLAIMER
