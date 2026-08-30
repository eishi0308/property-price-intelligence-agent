"""Turning selected comparables into a defensible price *range*.

WHAT THIS MODULE DELIBERATELY IS NOT
------------------------------------
It is not a valuation model. There is no regression, no hedonic adjustment, no
learned weighting, no time-series extrapolation. Nothing here predicts a price.

What it does is arithmetic a buyer could reproduce on paper: take the sale prices
of the comparables that survived retrieval and grading, describe their central
tendency and spread, and state where the asking price sits relative to them. The
output is evidence about a negotiation, not a number to trust blindly.

WHY THE INTERQUARTILE RANGE
---------------------------
Min-max is the obvious choice and the wrong one: a single atypical sale — a
deceased estate, a related-party transfer, one unusually large unit — stretches
the range until it says nothing. With five or more comparables we report the
interquartile range, which needs two atypical sales pointing the same way before
it moves, and we report the full observed span alongside it so nothing is hidden.
Below five comparables there is not enough data for quartiles to mean anything,
so we show the full span and cap confidence.

WHY CONFIDENCE CAN ONLY FALL
----------------------------
`derive_confidence` starts optimistic and applies penalties: too few comparables,
wide dispersion, stale sales, a widened search radius, a degraded embedding
backend, a rule-based grader. It has no path to raise confidence. That asymmetry
is intentional — every mechanism that can lower it is a known weakness, and none
of them are things a user can see for themselves.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.schemas.assessment import AssessmentLabel, ConfidenceLevel, EvidenceQuality

#: Asking price vs the evidence range, as a fraction of the range midpoint.
FAIR_BAND = 0.03  # within ±3% of the range is "reasonable"
SLIGHTLY_HIGH_BAND = 0.10  # up to 10% above the top of the range

MIN_COMPS_FOR_QUARTILES = 5
MIN_COMPS_FOR_ADEQUATE = 4
MIN_COMPS_FOR_STRONG = 6
#: Above this IQR/median ratio the comparables disagree too much to be "strong".
WIDE_DISPERSION = 0.18
VERY_WIDE_DISPERSION = 0.30


@dataclass
class ComparableSale:
    """The minimum a comparable must supply to influence the evidence range."""

    external_id: str
    address: str
    sold_price: int
    sold_at: str
    distance_km: float | None
    months_ago: float
    relevance: float


@dataclass
class PriceEvidence:
    """Everything the assessment says about price, computed arithmetically."""

    comparables_used: int
    evidence_low: int | None
    evidence_high: int | None
    median: int | None
    observed_low: int | None
    observed_high: int | None
    dispersion_ratio: float | None
    asking_price: int | None
    delta_to_high: int | None = None
    delta_to_median: int | None = None
    delta_pct_to_range: float | None = None
    method: str = "interquartile range of selected comparable sale prices"
    notes: list[str] = field(default_factory=list)

    @property
    def has_range(self) -> bool:
        return self.evidence_low is not None and self.evidence_high is not None


def compute_price_evidence(
    comparables: list[ComparableSale], asking_price: int | None
) -> PriceEvidence:
    """Describe the selected comparables. Pure arithmetic, no inference."""
    prices = sorted(item.sold_price for item in comparables if item.sold_price > 0)
    if not prices:
        return PriceEvidence(
            comparables_used=0,
            evidence_low=None,
            evidence_high=None,
            median=None,
            observed_low=None,
            observed_high=None,
            dispersion_ratio=None,
            asking_price=asking_price,
            method="no comparable sales available",
            notes=["No comparable sale prices were available to form a range."],
        )

    median = int(statistics.median(prices))
    notes: list[str] = []

    if len(prices) >= MIN_COMPS_FOR_QUARTILES:
        quartiles = statistics.quantiles(prices, n=4, method="inclusive")
        low, high = int(quartiles[0]), int(quartiles[2])
        method = (
            f"interquartile range of {len(prices)} selected comparable sale prices "
            f"(middle 50%; full observed span ${prices[0]:,}-${prices[-1]:,})"
        )
    else:
        low, high = prices[0], prices[-1]
        method = f"full observed span of {len(prices)} comparable sale prices"
        notes.append(
            f"Only {len(prices)} comparable sales were available, so the full observed "
            f"span is shown rather than an interquartile range."
        )

    dispersion = round((high - low) / median, 4) if median else None
    if dispersion is not None and dispersion > VERY_WIDE_DISPERSION:
        notes.append(
            f"The selected comparables disagree considerably (spread is "
            f"{dispersion:.0%} of the median), which widens the evidence range."
        )

    evidence = PriceEvidence(
        comparables_used=len(prices),
        evidence_low=low,
        evidence_high=high,
        median=median,
        observed_low=prices[0],
        observed_high=prices[-1],
        dispersion_ratio=dispersion,
        asking_price=asking_price,
        method=method,
        notes=notes,
    )

    if asking_price:
        evidence.delta_to_high = asking_price - high
        evidence.delta_to_median = asking_price - median
        midpoint = (low + high) / 2
        if midpoint:
            if asking_price > high:
                evidence.delta_pct_to_range = round((asking_price - high) / midpoint, 4)
            elif asking_price < low:
                evidence.delta_pct_to_range = round((asking_price - low) / midpoint, 4)
            else:
                evidence.delta_pct_to_range = 0.0
    return evidence


def derive_label(evidence: PriceEvidence, quality: EvidenceQuality) -> AssessmentLabel:
    """Map the arithmetic onto a label. Explicit thresholds, no model."""
    if not quality.is_sufficient or not evidence.has_range:
        return AssessmentLabel.INSUFFICIENT_EVIDENCE
    if evidence.asking_price is None:
        return AssessmentLabel.INSUFFICIENT_EVIDENCE

    assert evidence.evidence_low is not None and evidence.evidence_high is not None
    midpoint = (evidence.evidence_low + evidence.evidence_high) / 2
    if midpoint <= 0:
        return AssessmentLabel.INSUFFICIENT_EVIDENCE

    if evidence.asking_price < evidence.evidence_low * (1 - FAIR_BAND):
        return AssessmentLabel.UNDERPRICED
    if evidence.asking_price <= evidence.evidence_high * (1 + FAIR_BAND):
        return AssessmentLabel.FAIR
    overshoot = (evidence.asking_price - evidence.evidence_high) / midpoint
    if overshoot <= SLIGHTLY_HIGH_BAND:
        return AssessmentLabel.SLIGHTLY_HIGH
    return AssessmentLabel.HIGH


def assess_evidence_quality(
    *,
    included_count: int,
    dispersion_ratio: float | None,
    median_months_ago: float | None,
    median_distance_km: float | None,
    radius_km: float,
    expansions_used: int,
    degraded_embeddings: bool,
    degraded_grader: bool,
) -> tuple[EvidenceQuality, list[str]]:
    """Judge how much the evidence can actually support, and say why."""
    reasons: list[str] = []

    if included_count == 0:
        return EvidenceQuality.INSUFFICIENT, ["No comparable sales survived filtering and grading."]
    if included_count < MIN_COMPS_FOR_ADEQUATE:
        return (
            EvidenceQuality.INSUFFICIENT,
            [
                f"Only {included_count} comparable sale{'s' if included_count != 1 else ''} "
                f"could be supported; at least {MIN_COMPS_FOR_ADEQUATE} are needed for a "
                f"defensible range."
            ],
        )

    quality = (
        EvidenceQuality.STRONG
        if included_count >= MIN_COMPS_FOR_STRONG
        else EvidenceQuality.ADEQUATE
    )
    if included_count < MIN_COMPS_FOR_STRONG:
        reasons.append(f"{included_count} comparables is a workable but not deep evidence base.")

    def demote(reason: str) -> None:
        nonlocal quality
        reasons.append(reason)
        order = [
            EvidenceQuality.STRONG,
            EvidenceQuality.ADEQUATE,
            EvidenceQuality.WEAK,
            EvidenceQuality.INSUFFICIENT,
        ]
        quality = order[min(order.index(quality) + 1, len(order) - 1)]

    if dispersion_ratio is not None and dispersion_ratio > VERY_WIDE_DISPERSION:
        demote(f"Comparable prices are widely spread ({dispersion_ratio:.0%} of the median).")
    elif dispersion_ratio is not None and dispersion_ratio > WIDE_DISPERSION:
        reasons.append(
            f"Comparable prices show moderate spread ({dispersion_ratio:.0%} of the median)."
        )

    if median_months_ago is not None and median_months_ago > 12:
        demote(f"Half the comparables sold more than {median_months_ago:.0f} months ago.")
    if median_distance_km is not None and median_distance_km > 3.0:
        demote(f"Comparables average {median_distance_km:.1f} km away, beyond the immediate area.")
    if expansions_used > 0:
        reasons.append(
            f"The search was widened {expansions_used} time{'s' if expansions_used != 1 else ''} "
            f"(to {radius_km:g} km) to find enough comparables."
        )
    if degraded_embeddings:
        reasons.append(
            "Semantic matching ran on the offline deterministic encoder, so qualitative "
            "similarity is approximate."
        )
    if degraded_grader:
        reasons.append(
            "Comparables were graded by deterministic rules rather than a language model, "
            "so qualitative nuance is limited."
        )
    return quality, reasons


def derive_confidence(
    quality: EvidenceQuality,
    evidence: PriceEvidence,
    *,
    expansions_used: int,
    degraded_embeddings: bool,
    degraded_grader: bool,
) -> ConfidenceLevel:
    """Confidence can only ever be reduced from the evidence-quality ceiling."""
    ceiling = {
        EvidenceQuality.STRONG: ConfidenceLevel.HIGH,
        EvidenceQuality.ADEQUATE: ConfidenceLevel.MEDIUM,
        EvidenceQuality.WEAK: ConfidenceLevel.LOW,
        EvidenceQuality.INSUFFICIENT: ConfidenceLevel.LOW,
    }[quality]

    order = [ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM, ConfidenceLevel.LOW]
    index = order.index(ceiling)

    if evidence.dispersion_ratio is not None and evidence.dispersion_ratio > WIDE_DISPERSION:
        index += 1
    if expansions_used >= 2:
        index += 1
    if degraded_embeddings and degraded_grader:
        index += 1
    if evidence.comparables_used < MIN_COMPS_FOR_QUARTILES:
        index += 1
    return order[min(index, len(order) - 1)]
