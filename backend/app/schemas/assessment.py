"""Structured output contracts for the LLM-backed components.

These schemas are the *only* shape an LLM is allowed to emit. Anything outside
them is rejected by the guardrail layer rather than shown to a user who is about
to make a six- or seven-figure decision.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AssessmentLabel(str, Enum):
    UNDERPRICED = "underpriced"
    FAIR = "fair"
    SLIGHTLY_HIGH = "slightly_high"
    HIGH = "high"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"

    @property
    def display(self) -> str:
        return {
            AssessmentLabel.UNDERPRICED: "Below Evidence Range",
            AssessmentLabel.FAIR: "Reasonable",
            AssessmentLabel.SLIGHTLY_HIGH: "Slightly High",
            AssessmentLabel.HIGH: "High",
            AssessmentLabel.INSUFFICIENT_EVIDENCE: "Insufficient Evidence",
        }[self]


class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceQuality(str, Enum):
    INSUFFICIENT = "insufficient"
    WEAK = "weak"
    ADEQUATE = "adequate"
    STRONG = "strong"

    @property
    def is_sufficient(self) -> bool:
        return self in {EvidenceQuality.ADEQUATE, EvidenceQuality.STRONG}


class RerankVerdict(BaseModel):
    """Structured relevance grade for one candidate comparable sale.

    Produced either by an LLM grader (structured output) or by the deterministic
    offline grader. The grader may only *judge* facts it was given; it must never
    introduce a property, price or feature.
    """

    model_config = ConfigDict(extra="forbid")

    comparable: bool = Field(description="Is this a defensible comparable sale?")
    similarity_score: float = Field(
        ge=0.0, le=1.0, description="Overall comparability, 1.0 = near-identical"
    )
    important_matches: list[str] = Field(
        default_factory=list, max_length=8, description="Concrete shared characteristics"
    )
    important_differences: list[str] = Field(
        default_factory=list, max_length=8, description="Concrete differences that affect price"
    )
    reason: str = Field(default="", max_length=600)


class ComparableCitation(BaseModel):
    """A single comparable sale cited in the final assessment."""

    model_config = ConfigDict(extra="forbid")

    external_id: str
    address: str
    sold_price: int
    sold_at: str
    distance_km: float | None = None
    relevance: float = Field(ge=0.0, le=1.0)


class PriceAssessment(BaseModel):
    """The final, evidence-grounded answer.

    Deliberately NOT a point valuation. `evidence_range_*` is derived from the
    observed sold prices of the selected comparables by deterministic arithmetic;
    the LLM narrates and attributes, it does not compute the number.
    """

    model_config = ConfigDict(extra="forbid")

    assessment: AssessmentLabel
    asking_price: int | None = None
    evidence_range_low: int | None = None
    evidence_range_high: int | None = None
    evidence_median: int | None = None
    confidence: ConfidenceLevel
    reasoning_summary: str = Field(default="", max_length=2000)
    supporting_comparables: list[ComparableCitation] = Field(default_factory=list)
    important_differences: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)

    # Provenance — always surfaced so a reader knows how the answer was produced.
    generated_by: str = "unknown"
    evidence_quality: EvidenceQuality = EvidenceQuality.INSUFFICIENT
    guardrail_notes: list[str] = Field(default_factory=list)
    is_demo_data: bool = False


class AssessmentNarrative(BaseModel):
    """The narrow slice of the assessment an LLM is permitted to author.

    The numeric fields are computed deterministically and merged in afterwards,
    so the model literally cannot invent a price.
    """

    model_config = ConfigDict(extra="forbid")

    reasoning_summary: str = Field(max_length=2000)
    important_differences: list[str] = Field(default_factory=list, max_length=8)
    unknowns: list[str] = Field(default_factory=list, max_length=8)
    cited_comparable_ids: list[str] = Field(default_factory=list, max_length=12)
