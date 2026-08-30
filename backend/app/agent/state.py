"""Agent state.

WHY A STATEFUL GRAPH RATHER THAN A FUNCTION
-------------------------------------------
A straight-line pipeline can only answer "here is what I found". This workflow
has to answer "here is what I found, and here is what I did when it was not good
enough" — which requires the run to carry, and revise, its own search parameters:

    search_radius_km / lookback_months   revised when comparables are too few
    expansions_used / evidence_retries   loop counters that guarantee termination
    evidence_quality / missing_information  the self-assessment that drives routing

Those fields are the reason this is a graph. Without them the conditional edges
would have nothing to branch on and LangGraph would be decoration.

Everything here survives checkpointing, so a run can be inspected, resumed, or —
in the human-in-the-loop path — re-run with a comparable excluded, without
repeating the work that led up to it.
"""

from __future__ import annotations

from typing import Any, TypedDict

from app.rag.evidence import EvidenceBundle
from app.retrieval.hybrid import FusedCandidate
from app.retrieval.rerank import RerankedCandidate
from app.retrieval.sql_filter import CandidateRow, FunnelCounts
from app.schemas.assessment import PriceAssessment
from app.schemas.property import ListingRecord, PropertyRecord


class PropertyAnalysisState(TypedDict, total=False):
    # --- inputs ---------------------------------------------------------------
    analysis_id: str
    query: str
    asking_price_override: int | None
    user_excluded_external_ids: list[str]

    # --- resolved target ------------------------------------------------------
    target_property: PropertyRecord | None
    target_property_db_id: str | None
    target_listing: ListingRecord | None
    target_text: str
    target_embedding: list[float]
    asking_price: int | None
    asking_price_source: str | None
    interpreted_as: str
    resolution_candidates: list[dict[str, Any]]

    # --- retrieval ------------------------------------------------------------
    sold_candidate_count: int
    structured_candidates: list[CandidateRow]
    funnel: FunnelCounts | None
    filter_criteria: list[str]
    semantic_candidates: list[FusedCandidate]
    reranked_comps: list[RerankedCandidate]

    # --- adaptive search parameters (the reason this is a graph) --------------
    search_radius_km: float
    lookback_months: int
    expansions_used: int
    evidence_retries: int

    # --- evidence & self-assessment -------------------------------------------
    evidence: EvidenceBundle | None
    evidence_quality: str
    evidence_quality_reasons: list[str]
    missing_information: list[str]

    # --- output ---------------------------------------------------------------
    assessment: PriceAssessment | None
    stages: list[dict[str, Any]]
    tool_history: list[dict[str, Any]]
    guardrail_notes: list[str]
    degraded_embeddings: bool
    degraded_grader: bool
    error: str | None
    failed_stage: str | None


def initial_state(
    *,
    analysis_id: str,
    query: str,
    radius_km: float,
    lookback_months: int,
    asking_price_override: int | None = None,
    user_excluded_external_ids: list[str] | None = None,
) -> PropertyAnalysisState:
    return PropertyAnalysisState(
        analysis_id=analysis_id,
        query=query,
        asking_price_override=asking_price_override,
        user_excluded_external_ids=user_excluded_external_ids or [],
        search_radius_km=radius_km,
        lookback_months=lookback_months,
        expansions_used=0,
        evidence_retries=0,
        structured_candidates=[],
        semantic_candidates=[],
        reranked_comps=[],
        missing_information=[],
        stages=[],
        tool_history=[],
        guardrail_notes=[],
        evidence_quality="unknown",
        evidence_quality_reasons=[],
        degraded_embeddings=False,
        degraded_grader=False,
        error=None,
        failed_stage=None,
    )
