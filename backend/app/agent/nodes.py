"""Graph nodes.

Each node is an async function that receives the state and returns a *partial*
update. Three conventions hold throughout:

* **Nodes never raise.** A failure is recorded in `error` / `failed_stage` and the
  graph routes to a terminal failure node. A half-finished analysis that explains
  where it stopped is far more useful than a stack trace.
* **External access goes through MCP tools**, never through provider objects
  directly, so the tool history is a complete record of what the agent touched.
* **Every node appends a stage entry**, which is what the UI's progress panel and
  the `/trace` endpoint render.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.assessment.pricing import (
    ComparableSale,
    assess_evidence_quality,
    compute_price_evidence,
    derive_confidence,
    derive_label,
)
from app.config import get_settings
from app.db import repository
from app.db.engine import session_scope
from app.guardrails import DISCLAIMER, apply_guardrails
from app.mcp_servers import get_toolset
from app.observability import TraceRecorder, get_logger
from app.rag.chains import generate_narrative
from app.rag.evidence import EvidenceBundle, retrieve_supporting_evidence
from app.retrieval.embeddings import get_embeddings
from app.retrieval.hybrid import hybrid_retrieve
from app.retrieval.indexer import ensure_indexed
from app.retrieval.rerank import DeterministicRelevanceGrader, rerank_candidates
from app.retrieval.semantic_text import build_query_document
from app.retrieval.sql_filter import criteria_for, hard_filter
from app.retrieval.vector_store import SOURCE_LISTING
from app.schemas.analysis import PipelineStage
from app.schemas.assessment import (
    AssessmentLabel,
    ComparableCitation,
    EvidenceQuality,
    PriceAssessment,
)
from app.schemas.property import ListingRecord, PropertyRecord

logger = get_logger(__name__)

#: Set per-run by `graph.run_analysis` so nodes can record trace entries.
_TRACERS: dict[str, TraceRecorder] = {}


def register_tracer(analysis_id: str, tracer: TraceRecorder) -> None:
    _TRACERS[analysis_id] = tracer


def release_tracer(analysis_id: str) -> None:
    _TRACERS.pop(analysis_id, None)


def _tracer(state: dict[str, Any]) -> TraceRecorder:
    return _TRACERS.setdefault(state.get("analysis_id", "unknown"), TraceRecorder())


def _stage(
    stage: PipelineStage, label: str, detail: str | None = None, ok: bool = True
) -> dict[str, Any]:
    return {
        "stage": stage.value,
        "label": label,
        "detail": detail,
        "ok": ok,
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _fail(state: dict[str, Any], stage: PipelineStage, message: str) -> dict[str, Any]:
    _tracer(state).record("node", stage.value, status="error", error=message)
    logger.warning("agent.node_failed", stage=stage.value, error=message)
    return {
        "error": message,
        "failed_stage": stage.value,
        "stages": [
            *state.get("stages", []),
            _stage(stage, stage.value.replace("_", " ").title(), message, ok=False),
        ],
    }


async def _call_tool(state: dict[str, Any], name: str, /, **kwargs: Any) -> dict[str, Any]:
    """Invoke an MCP tool and record it in the tool history.

    `state` and `name` are positional-only: several MCP tools take their own
    `state` argument (the Australian state), and without the `/` that keyword
    binds to this function's first parameter instead of being forwarded,
    raising "multiple values for argument 'state'".
    """
    toolset = await get_toolset()
    tracer = _tracer(state)
    started = time.perf_counter()
    result = await toolset.call(name, **kwargs)
    duration_ms = int((time.perf_counter() - started) * 1000)
    tracer.record(
        "tool_call",
        name,
        status="ok" if result.get("ok") else "error",
        duration_ms=duration_ms,
        transport=toolset.transport,
        arguments={key: value for key, value in kwargs.items() if key != "query_text"},
        error=result.get("error"),
    )
    state.setdefault("tool_history", []).append(
        {
            "tool": name,
            "transport": toolset.transport,
            "arguments": kwargs,
            "ok": bool(result.get("ok")),
            "duration_ms": duration_ms,
            "error": result.get("error"),
        }
    )
    return result


# ---------------------------------------------------------------------------
# 1. Resolve property
# ---------------------------------------------------------------------------
async def resolve_property_node(state: dict[str, Any]) -> dict[str, Any]:
    result = await _call_tool(state, "resolve_property", query=state["query"])
    if not result.get("ok"):
        update = _fail(
            state, PipelineStage.RESOLVE, result.get("error", "Could not resolve the property.")
        )
        update["resolution_candidates"] = result.get("candidates", [])
        update["interpreted_as"] = result.get("interpreted_as", "")
        return update

    record = PropertyRecord.model_validate(result["property"])
    if record.coordinates is None:
        return _fail(
            state,
            PipelineStage.RESOLVE,
            "The provider returned no coordinates for this property, so nearby sales "
            "cannot be searched. This is a data limitation, not a transient error.",
        )
    async with session_scope() as session:
        property_db_id = await repository.upsert_property(session, record)

    return {
        "target_property": record,
        "target_property_db_id": str(property_db_id),
        "interpreted_as": result.get("interpreted_as", ""),
        "stages": [
            *state.get("stages", []),
            _stage(PipelineStage.RESOLVE, "Property identified", record.address),
        ],
    }


# ---------------------------------------------------------------------------
# 2. Fetch target data
# ---------------------------------------------------------------------------
async def fetch_target_data_node(state: dict[str, Any]) -> dict[str, Any]:
    target: PropertyRecord = state["target_property"]
    listing: ListingRecord | None = None
    missing: list[str] = list(state.get("missing_information", []))

    result = await _call_tool(state, "get_current_listing", external_id=target.external_id)
    if result.get("ok"):
        listing = ListingRecord.model_validate(result["listing"])
        async with session_scope() as session:
            await repository.upsert_listing(
                session, listing, uuid.UUID(state["target_property_db_id"])
            )
    else:
        missing.append(
            "No current listing was available for the target property, so no advertised "
            "description or price could be used."
        )

    asking_price = state.get("asking_price_override")
    asking_price_source = "user_supplied" if asking_price else None
    if asking_price is None and listing and listing.asking_price:
        asking_price, asking_price_source = listing.asking_price, "listing"
    if asking_price is None:
        missing.append(
            "No asking price is published for this property. Supply one to compare it "
            "against the evidence range."
        )

    document = build_query_document(target, listing)
    if document.is_degraded:
        missing.append(
            "The target has no usable listing description, so qualitative matching relies "
            "on structured attributes alone."
        )
    embedding = await get_embeddings().aembed_query(document.text)

    settings = get_settings()
    return {
        "target_listing": listing,
        "target_text": document.text,
        "target_embedding": embedding,
        "asking_price": asking_price,
        "asking_price_source": asking_price_source,
        "missing_information": missing,
        "degraded_embeddings": settings.resolved_embedding_backend.value == "offline",
        "stages": [
            *state.get("stages", []),
            _stage(
                PipelineStage.FETCH_TARGET,
                "Target listing retrieved",
                f"Asking price ${asking_price:,}" if asking_price else "No asking price published",
            ),
        ],
    }


# ---------------------------------------------------------------------------
# 3. Search sold candidates  (MCP tool call; loop target for expansion)
# ---------------------------------------------------------------------------
async def search_sold_candidates_node(state: dict[str, Any]) -> dict[str, Any]:
    target: PropertyRecord = state["target_property"]
    assert target.coordinates is not None
    result = await _call_tool(
        state,
        "search_sold_properties",
        latitude=target.coordinates.latitude,
        longitude=target.coordinates.longitude,
        radius_km=state["search_radius_km"],
        lookback_months=state["lookback_months"],
        property_types=sorted(item.value for item in target.property_type.compatible_types),
        limit=200,
    )
    if not result.get("ok"):
        return _fail(
            state, PipelineStage.SEARCH_SOLD, result.get("error", "Sold-sales search failed.")
        )

    count = int(result.get("count", 0))
    return {
        "sold_candidate_count": count,
        "stages": [
            *state.get("stages", []),
            _stage(
                PipelineStage.SEARCH_SOLD,
                f"{count} nearby sales found",
                f"Within {state['search_radius_km']:g} km over {state['lookback_months']} months",
                ok=count > 0,
            ),
        ],
    }


# ---------------------------------------------------------------------------
# 4. Hard filter (Stage A, SQL)
# ---------------------------------------------------------------------------
async def hard_filter_node(state: dict[str, Any]) -> dict[str, Any]:
    target: PropertyRecord = state["target_property"]
    settings = get_settings()
    exclude = frozenset({uuid.UUID(state["target_property_db_id"])})
    criteria = criteria_for(
        target,
        radius_km=state["search_radius_km"],
        lookback_months=state["lookback_months"],
        exclude_property_ids=exclude,
        limit=settings.hard_filter_limit,
    )
    tracer = _tracer(state)
    async with session_scope() as session:
        with tracer.span(
            "node", PipelineStage.HARD_FILTER.value, radius_km=state["search_radius_km"]
        ) as span:
            result = await hard_filter(session, criteria)
            span.annotate(candidates=len(result.candidates))
        indexed, degraded = await ensure_indexed(
            session, [item.property_id for item in result.candidates]
        )
    if indexed:
        tracer.record("retrieval", "ensure_indexed", indexed=indexed, degraded_chunks=degraded)

    return {
        "structured_candidates": result.candidates,
        "funnel": result.funnel,
        "filter_criteria": result.criteria_applied,
        "stages": [
            *state.get("stages", []),
            _stage(
                PipelineStage.HARD_FILTER,
                f"{len(result.candidates)} structural candidates",
                " ".join(result.funnel.to_stage_lines()[1:]),
                ok=bool(result.candidates),
            ),
        ],
    }


# ---------------------------------------------------------------------------
# 5. Hybrid retrieval (Stages B-D)
# ---------------------------------------------------------------------------
async def hybrid_retrieval_node(state: dict[str, Any]) -> dict[str, Any]:
    candidates = state.get("structured_candidates") or []
    if not candidates:
        return {
            "semantic_candidates": [],
            "stages": [
                *state.get("stages", []),
                _stage(PipelineStage.HYBRID_RETRIEVAL, "No candidates to rank", None, ok=False),
            ],
        }
    tracer = _tracer(state)
    async with session_scope() as session:
        with tracer.span("node", PipelineStage.HYBRID_RETRIEVAL.value) as span:
            fused = await hybrid_retrieve(
                session,
                target=state["target_property"],
                target_document=build_query_document(
                    state["target_property"], state.get("target_listing")
                ),
                target_embedding=state["target_embedding"],
                candidates=candidates,
                radius_km=state["search_radius_km"],
            )
            span.annotate(
                fused=len(fused),
                keyword_hits=sum(1 for item in fused if item.keyword_rank is not None),
                semantic_hits=sum(1 for item in fused if item.vector_rank is not None),
            )
    return {
        "semantic_candidates": fused,
        "stages": [
            *state.get("stages", []),
            _stage(
                PipelineStage.HYBRID_RETRIEVAL,
                "Semantic matching complete",
                f"{len(fused)} candidates ranked by structural, keyword and semantic signals",
            ),
        ],
    }


# ---------------------------------------------------------------------------
# 6. Rerank (Stage E)
# ---------------------------------------------------------------------------
async def rerank_node(state: dict[str, Any]) -> dict[str, Any]:
    fused = state.get("semantic_candidates") or []
    if not fused:
        return {
            "reranked_comps": [],
            "stages": [
                *state.get("stages", []),
                _stage(PipelineStage.RERANK, "No candidates to grade", None, ok=False),
            ],
        }

    excluded = set(state.get("user_excluded_external_ids") or [])
    result = await rerank_candidates(
        target=state["target_property"],
        target_text=state["target_text"],
        fused=fused,
        tracer=_tracer(state),
    )

    # Human-in-the-loop: a user exclusion overrides the grader, and the analysis
    # is recomputed without that sale rather than merely hiding it.
    if excluded:
        promoted = 0
        for item in result.ranked:
            if item.fused.candidate.external_id in excluded:
                item.included = False
                item.exclusion_reason = "Excluded by the user."
        settings = get_settings()
        included = [item for item in result.ranked if item.included]
        for item in result.ranked:
            if len(included) + promoted >= settings.target_comparables:
                break
            if (
                not item.included
                and item.fused.candidate.external_id not in excluded
                and item.verdict.comparable
                and item.exclusion_reason
                and item.exclusion_reason.startswith("Outside the strongest")
            ):
                item.included = True
                item.exclusion_reason = None
                promoted += 1

    included_count = len(result.included)
    return {
        "reranked_comps": result.ranked,
        "degraded_grader": result.graded_by == DeterministicRelevanceGrader.name,
        "guardrail_notes": [*state.get("guardrail_notes", []), *result.guardrail_notes],
        "stages": [
            *state.get("stages", []),
            _stage(
                PipelineStage.RERANK,
                f"{included_count} strong comparables identified",
                f"Graded by {result.graded_by}",
                ok=included_count > 0,
            ),
        ],
    }


# ---------------------------------------------------------------------------
# 7. Evaluate evidence  (the self-assessment that drives routing)
# ---------------------------------------------------------------------------
async def evaluate_evidence_node(state: dict[str, Any]) -> dict[str, Any]:
    included = [item for item in (state.get("reranked_comps") or []) if item.included]
    prices = [item.sold_price for item in included]
    dispersion = None
    if len(prices) >= 2:
        evidence = compute_price_evidence(
            [
                ComparableSale(
                    external_id=item.fused.candidate.external_id,
                    address=item.fused.candidate.address,
                    sold_price=item.sold_price,
                    sold_at=item.fused.candidate.sold_at.isoformat(),
                    distance_km=item.fused.candidate.distance_km,
                    months_ago=item.fused.candidate.months_ago,
                    relevance=item.verdict.similarity_score,
                )
                for item in included
            ],
            state.get("asking_price"),
        )
        dispersion = evidence.dispersion_ratio

    months = sorted(item.fused.candidate.months_ago for item in included)
    distances = sorted(item.fused.candidate.distance_km for item in included)
    quality, reasons = assess_evidence_quality(
        included_count=len(included),
        dispersion_ratio=dispersion,
        median_months_ago=months[len(months) // 2] if months else None,
        median_distance_km=distances[len(distances) // 2] if distances else None,
        radius_km=state["search_radius_km"],
        expansions_used=state.get("expansions_used", 0),
        degraded_embeddings=bool(state.get("degraded_embeddings")),
        degraded_grader=bool(state.get("degraded_grader")),
    )
    _tracer(state).record(
        "node",
        PipelineStage.EVALUATE_EVIDENCE.value,
        included=len(included),
        quality=quality.value,
        dispersion=dispersion,
    )
    return {
        "evidence_quality": quality.value,
        "evidence_quality_reasons": reasons,
        "stages": [
            *state.get("stages", []),
            _stage(
                PipelineStage.EVALUATE_EVIDENCE,
                f"Evidence quality: {quality.value}",
                reasons[0] if reasons else f"{len(included)} comparables selected",
                ok=quality.is_sufficient,
            ),
        ],
    }


# ---------------------------------------------------------------------------
# 8. Expand search (loop back to the sold-sales search)
# ---------------------------------------------------------------------------
async def expand_search_node(state: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    radius = min(
        state["search_radius_km"] + settings.radius_expansion_step_km, settings.max_radius_km
    )
    lookback = min(
        state["lookback_months"] + settings.lookback_expansion_step_months,
        settings.max_lookback_months,
    )
    expansions = state.get("expansions_used", 0) + 1
    _tracer(state).record(
        "node",
        PipelineStage.EXPAND_SEARCH.value,
        from_radius=state["search_radius_km"],
        to_radius=radius,
        from_lookback=state["lookback_months"],
        to_lookback=lookback,
        attempt=expansions,
    )
    return {
        "search_radius_km": radius,
        "lookback_months": lookback,
        "expansions_used": expansions,
        "stages": [
            *state.get("stages", []),
            _stage(
                PipelineStage.EXPAND_SEARCH,
                "Widening the search",
                f"Not enough strong comparables; expanding to {radius:g} km / {lookback} months",
            ),
        ],
    }


# ---------------------------------------------------------------------------
# 9. Retrieve supporting evidence (RAG)
# ---------------------------------------------------------------------------
async def retrieve_evidence_node(state: dict[str, Any]) -> dict[str, Any]:
    included = [item for item in (state.get("reranked_comps") or []) if item.included]
    labels = {item.fused.candidate.property_id: item.fused.candidate.address for item in included}
    target_db_id = (
        uuid.UUID(state["target_property_db_id"]) if state.get("target_property_db_id") else None
    )
    async with session_scope() as session:
        bundle = await retrieve_supporting_evidence(
            session,
            target=state["target_property"],
            target_property_id=target_db_id,
            included_property_ids=[item.fused.candidate.property_id for item in included],
            comparable_labels=labels,
            target_embedding=state["target_embedding"],
            tracer=_tracer(state),
        )
    return {
        "evidence": bundle,
        "missing_information": [*state.get("missing_information", []), *bundle.missing],
        "stages": [
            *state.get("stages", []),
            _stage(
                PipelineStage.RETRIEVE_EVIDENCE,
                "Market evidence retrieved",
                ", ".join(
                    f"{count} {name.replace('_', ' ')}" for name, count in bundle.coverage().items()
                ),
                ok=bundle.is_sufficient,
            ),
        ],
    }


# ---------------------------------------------------------------------------
# 10. Research more  (tool calling driven by what is missing)
# ---------------------------------------------------------------------------
async def research_more_node(state: dict[str, Any]) -> dict[str, Any]:
    """Targeted extra retrieval when the evidence bundle is thin.

    The tool chosen depends on *which* gap exists — a missing market picture and
    a qualitatively mismatched comparable set are different problems.
    """
    target: PropertyRecord = state["target_property"]
    bundle = state.get("evidence")
    coverage = bundle.coverage() if bundle else {}
    retries = state.get("evidence_retries", 0) + 1
    actions: list[str] = []

    if coverage.get("market_context", 0) == 0:
        result = await _call_tool(
            state,
            "get_market_context",
            suburb=target.suburb,
            state=target.state,
            postcode=target.postcode,
            property_type=target.property_type.value,
        )
        actions.append(
            "fetched suburb market context" if result.get("ok") else "market context unavailable"
        )

    if coverage.get(SOURCE_LISTING, 0) < 3:
        result = await _call_tool(
            state,
            "search_similar_properties",
            query_text=state.get("target_text", target.address),
            limit=12,
            suburb=target.suburb,
        )
        actions.append(
            f"semantic sweep returned {result.get('count', 0)} similar listings"
            if result.get("ok")
            else "semantic sweep failed"
        )

    if state.get("asking_price") and coverage.get("market_context", 0) < 2:
        result = await _call_tool(
            state,
            "retrieve_market_evidence",
            query_text=(
                f"What explains sale prices for {target.property_type.value}s in "
                f"{target.suburb} around ${state['asking_price']:,}?"
            ),
            suburb=target.suburb,
            limit=4,
        )
        actions.append(
            f"retrieved {result.get('count', 0)} market evidence chunks"
            if result.get("ok")
            else "no market evidence"
        )

    return {
        "evidence_retries": retries,
        "stages": [
            *state.get("stages", []),
            _stage(
                PipelineStage.RESEARCH_MORE,
                "Researching further",
                "; ".join(actions) or "no additional sources available",
            ),
        ],
    }


# ---------------------------------------------------------------------------
# 11. Assess price (deterministic maths + RAG narration)
# ---------------------------------------------------------------------------
async def assess_price_node(state: dict[str, Any]) -> dict[str, Any]:
    included = [item for item in (state.get("reranked_comps") or []) if item.included]

    comparables = [
        ComparableSale(
            external_id=item.fused.candidate.external_id,
            address=item.fused.candidate.address,
            sold_price=item.sold_price,
            sold_at=item.fused.candidate.sold_at.isoformat(),
            distance_km=item.fused.candidate.distance_km,
            months_ago=item.fused.candidate.months_ago,
            relevance=item.verdict.similarity_score,
        )
        for item in included
    ]
    evidence = compute_price_evidence(comparables, state.get("asking_price"))
    quality = EvidenceQuality(state.get("evidence_quality", "insufficient"))
    label = derive_label(evidence, quality)
    confidence = derive_confidence(
        quality,
        evidence,
        expansions_used=state.get("expansions_used", 0),
        degraded_embeddings=bool(state.get("degraded_embeddings")),
        degraded_grader=bool(state.get("degraded_grader")),
    )

    # assess_price is only reachable through retrieve_supporting_evidence, whose
    # single return always sets "evidence" -- index rather than .get() so a broken
    # graph edge fails here by name instead of deep inside the narration chain.
    bundle: EvidenceBundle = state["evidence"]
    narrative_result = await generate_narrative(
        target=state["target_property"],
        target_text=state.get("target_text", ""),
        evidence=evidence,
        bundle=bundle,
        comparables=included,
        label=label.value,
        confidence=confidence.value,
        tracer=_tracer(state),
    )
    narrative = narrative_result.narrative

    assessment = PriceAssessment(
        assessment=label,
        asking_price=state.get("asking_price"),
        evidence_range_low=evidence.evidence_low,
        evidence_range_high=evidence.evidence_high,
        evidence_median=evidence.median,
        confidence=confidence,
        reasoning_summary=narrative.reasoning_summary,
        supporting_comparables=[
            ComparableCitation(
                external_id=item.fused.candidate.external_id,
                address=item.fused.candidate.address,
                sold_price=item.sold_price,
                sold_at=item.fused.candidate.sold_at.isoformat(),
                distance_km=item.fused.candidate.distance_km,
                relevance=item.verdict.similarity_score,
            )
            for item in included
        ],
        important_differences=narrative.important_differences,
        unknowns=[*narrative.unknowns, *state.get("evidence_quality_reasons", [])][:10],
        citations=narrative.cited_comparable_ids,
        generated_by=narrative_result.generated_by,
        evidence_quality=quality,
        guardrail_notes=(
            [narrative_result.fallback_reason] if narrative_result.fallback_reason else []
        ),
        is_demo_data=bool(state["target_property"].is_demo_data),
    )
    _tracer(state).record(
        "node",
        PipelineStage.ASSESS.value,
        label=label.value,
        confidence=confidence.value,
        comparables=len(included),
        generated_by=narrative_result.generated_by,
    )
    return {
        "assessment": assessment,
        "stages": [
            *state.get("stages", []),
            _stage(
                PipelineStage.ASSESS,
                "Assessment complete",
                f"{label.display} — {confidence.value} confidence",
            ),
        ],
    }


# ---------------------------------------------------------------------------
# 12. Guardrail check
# ---------------------------------------------------------------------------
async def guardrail_node(state: dict[str, Any]) -> dict[str, Any]:
    assessment: PriceAssessment | None = state.get("assessment")
    if assessment is None:
        return _fail(state, PipelineStage.GUARDRAIL, "No assessment was produced to validate.")

    bundle = state.get("evidence")
    included = [item for item in (state.get("reranked_comps") or []) if item.included]
    allowed = {
        item.fused.candidate.external_id: ComparableCitation(
            external_id=item.fused.candidate.external_id,
            address=item.fused.candidate.address,
            sold_price=item.sold_price,
            sold_at=item.fused.candidate.sold_at.isoformat(),
            distance_km=item.fused.candidate.distance_km,
            relevance=item.verdict.similarity_score,
        )
        for item in included
    }
    # Figures the deterministic pricing layer derived; legitimately citable even
    # though they appear verbatim in no source document.
    derived: set[int] = set()
    if assessment.asking_price and assessment.evidence_range_high:
        derived.add(assessment.asking_price - assessment.evidence_range_high)
    if assessment.asking_price and assessment.evidence_range_low:
        derived.add(assessment.asking_price - assessment.evidence_range_low)
    if assessment.asking_price and assessment.evidence_median:
        derived.add(assessment.asking_price - assessment.evidence_median)
    if assessment.evidence_range_high and assessment.evidence_range_low:
        derived.add(assessment.evidence_range_high - assessment.evidence_range_low)

    cleaned, report = apply_guardrails(
        assessment,
        allowed_citation_ids=bundle.citation_ids if bundle else set(),
        allowed_comparables=allowed,
        evidence_text=bundle.to_prompt_block() if bundle else "",
        evidence_quality=EvidenceQuality(state.get("evidence_quality", "insufficient")),
        derived_figures=derived,
    )
    cleaned = cleaned.model_copy(
        update={"guardrail_notes": [*assessment.guardrail_notes, *report.notes]}
    )
    _tracer(state).record(
        "guardrail",
        "apply_guardrails",
        status="ok" if report.passed_clean else "repaired",
        blocked_claims=len(report.blocked_claims),
        removed_citations=len(report.removed_citations),
        unsupported_figures=len(report.unsupported_figures),
        confidence_downgraded=report.confidence_downgraded,
    )
    return {
        "assessment": cleaned,
        "guardrail_notes": [*state.get("guardrail_notes", []), *report.notes],
        "stages": [
            *state.get("stages", []),
            _stage(
                PipelineStage.GUARDRAIL,
                "Guardrail check passed" if report.passed_clean else "Guardrail repairs applied",
                DISCLAIMER if report.passed_clean else "; ".join(report.notes)[:220],
            ),
        ],
    }


# ---------------------------------------------------------------------------
# 13. Terminals
# ---------------------------------------------------------------------------
async def finalise_node(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "stages": [*state.get("stages", []), _stage(PipelineStage.COMPLETE, "Analysis complete")],
    }


async def failure_node(state: dict[str, Any]) -> dict[str, Any]:
    error = state.get("error") or "The analysis could not be completed."
    # Failure can happen before the target is resolved, so this really is optional.
    target: PropertyRecord | None = state.get("target_property")
    assessment = PriceAssessment(
        assessment=AssessmentLabel.INSUFFICIENT_EVIDENCE,
        asking_price=state.get("asking_price"),
        confidence="low",  # type: ignore[arg-type]
        reasoning_summary=(
            f"The analysis stopped at the '{state.get('failed_stage', 'unknown')}' stage: {error} "
            f"No price assessment can be made from the evidence gathered."
        ),
        unknowns=[error, *state.get("missing_information", [])][:8],
        evidence_quality=EvidenceQuality.INSUFFICIENT,
        generated_by="deterministic:failure-path",
        is_demo_data=bool(target.is_demo_data if target else False),
    )
    return {
        "assessment": assessment,
        "stages": [
            *state.get("stages", []),
            _stage(PipelineStage.FAILED, "Analysis stopped", error, ok=False),
        ],
    }
