"""API-facing analysis contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.assessment import EvidenceQuality, PriceAssessment
from app.schemas.property import PropertyRecord


class PipelineStage(str, Enum):
    RESOLVE = "resolve_property"
    FETCH_TARGET = "fetch_target_data"
    SEARCH_SOLD = "search_sold_candidates"
    HARD_FILTER = "hard_filter"
    HYBRID_RETRIEVAL = "hybrid_retrieval"
    RERANK = "rerank"
    EVALUATE_EVIDENCE = "evaluate_evidence"
    EXPAND_SEARCH = "expand_search"
    RETRIEVE_EVIDENCE = "retrieve_supporting_evidence"
    RESEARCH_MORE = "research_more"
    ASSESS = "assess_price"
    GUARDRAIL = "guardrail_check"
    COMPLETE = "complete"
    FAILED = "failed"


class AnalysisStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class AnalysisCreateRequest(BaseModel):
    """`POST /api/analysis` body."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=4,
        max_length=500,
        description="A supported property URL or an Australian street address.",
    )
    asking_price_override: int | None = Field(
        default=None,
        ge=10_000,
        le=100_000_000,
        description="Use when the listing shows no price, or shows a range.",
    )


class ComparableView(BaseModel):
    """One comparable sale as shown in the UI."""

    id: str
    external_id: str
    address: str
    suburb: str
    sold_price: int
    sold_at: str
    distance_km: float | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    carspaces: int | None = None
    floor_area_sqm: float | None = None
    land_area_sqm: float | None = None
    property_type: str

    # Retrieval provenance — each score labelled for what it actually is.
    vector_similarity: float | None = Field(
        default=None, description="Cosine similarity of listing-description embeddings (0-1)."
    )
    keyword_score: float | None = Field(
        default=None, description="PostgreSQL full-text rank over listing text."
    )
    fusion_score: float | None = Field(default=None, description="Reciprocal-rank-fusion score.")
    rerank_score: float | None = Field(
        default=None, description="Grader's comparability judgement (0-1)."
    )
    final_rank: int | None = None

    important_matches: list[str] = Field(default_factory=list)
    important_differences: list[str] = Field(default_factory=list)
    included: bool = True
    exclusion_reason: str | None = None
    excluded_by_user: bool = False
    source: str = "unknown"
    source_url: str | None = None
    description_excerpt: str | None = None
    is_demo_data: bool = False


class EvidenceView(BaseModel):
    """A retrieved evidence chunk shown in the Evidence panel."""

    id: str
    source_type: str
    title: str
    content: str
    why_it_matters: str
    source: str
    source_url: str | None = None
    published_at: str | None = None
    similarity: float | None = None
    is_demo_data: bool = False


class StageProgress(BaseModel):
    stage: PipelineStage
    label: str
    detail: str | None = None
    completed_at: str | None = None
    ok: bool = True


class TraceEvent(BaseModel):
    """One recorded step of the agent run — node transition or tool call."""

    sequence: int
    kind: str  # node | tool_call | retrieval | llm | guardrail
    name: str
    status: str = "ok"
    duration_ms: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    at: str | None = None


class AnalysisSummary(BaseModel):
    id: str
    status: AnalysisStatus
    query: str
    created_at: str
    address: str | None = None
    assessment: str | None = None
    confidence: str | None = None
    asking_price: int | None = None


class RunMetadata(BaseModel):
    """How this answer was produced — always shown, never hidden."""

    provider: str
    provider_is_demo: bool
    llm_backend: str
    embedding_backend: str
    embedding_model: str
    tracing_enabled: bool
    vector_retrieval_quality: str
    search_radius_km: float
    lookback_months: int
    expansions_used: int
    duration_ms: int | None = None

    # How the agent reached its tools, and what it actually called.
    mcp_transport: str = "unknown"
    mcp_degraded_reason: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisDetail(BaseModel):
    """`GET /api/analysis/{id}` response."""

    id: str
    status: AnalysisStatus
    query: str
    created_at: str
    target_property: PropertyRecord | None = None
    target_description: str | None = None
    asking_price: int | None = None
    asking_price_source: str | None = None
    assessment: PriceAssessment | None = None
    comparables: list[ComparableView] = Field(default_factory=list)
    evidence: list[EvidenceView] = Field(default_factory=list)
    stages: list[StageProgress] = Field(default_factory=list)
    evidence_quality: EvidenceQuality | None = None
    missing_information: list[str] = Field(default_factory=list)
    metadata: RunMetadata | None = None
    error: str | None = None
    disclaimer: str = (
        "This is an evidence-based comparable-sales analysis, not a professional "
        "property valuation or financial advice."
    )


class ExcludeComparableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=280)


class FeasibilityCheck(BaseModel):
    name: str
    passed: bool
    detail: str
    sample: str | None = None


class DataFeasibilityReport(BaseModel):
    """Output of the Phase-1 data gate (`scripts/check_property_data.py`)."""

    provider: str
    provider_is_demo: bool
    generated_at: datetime
    checks: list[FeasibilityCheck]
    vector_retrieval_viable: str  # YES | LIMITED | NO
    vector_retrieval_reason: str | None = None
    embedding_backend: str
    notes: list[str] = Field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(check.passed for check in self.checks)
