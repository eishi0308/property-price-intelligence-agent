"""The LangGraph workflow.

    START
      ↓
    resolve_property
      ↓
    fetch_target_data
      ↓
    search_sold_candidates  ←──────────────┐
      ↓                                    │
    hard_filter                            │
      ↓                                    │
    hybrid_retrieval                       │
      ↓                                    │
    rerank                                 │
      ↓                                    │
    evaluate_evidence                      │
      ↓                                    │
    [enough strong comparables?] ── no ──→ expand_search
      │ yes
      ↓
    retrieve_supporting_evidence  ←────────┐
      ↓                                    │
    [evidence sufficient?] ─────── no ───→ research_more
      │ yes
      ↓
    assess_price
      ↓
    guardrail_check
      ↓
    finalise → END

TWO LOOPS, BOTH BOUNDED
-----------------------
The outer loop widens the geographic and temporal search when too few defensible
comparables survive grading. The inner loop performs targeted extra retrieval
when the evidence bundle cannot support an explanation. Each has a hard counter
(`expansions_used`, `evidence_retries`) checked in the routing function, so the
graph provably terminates. A loop that could run forever on thin data would be a
liability, not a feature.

WHEN THE LOOPS ARE EXHAUSTED, THE ANSWER IS "INSUFFICIENT EVIDENCE"
------------------------------------------------------------------
Routing deliberately proceeds to assessment after the retry budget is spent
rather than failing. `evidence_quality` is carried forward honestly, the label
becomes `insufficient_evidence`, and the user is told what was missing. That is
the useful answer for a buyer — far more so than an error.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.state import PropertyAnalysisState, initial_state
from app.config import get_settings
from app.observability import TraceRecorder, configure_tracing, get_logger
from app.schemas.assessment import EvidenceQuality

logger = get_logger(__name__)

#: Called with the state snapshot after each node completes.
ProgressCallback = Callable[["PropertyAnalysisState"], Awaitable[None]]


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def _has_error(state: PropertyAnalysisState) -> bool:
    return bool(state.get("error"))


def route_after_resolve(state: PropertyAnalysisState) -> Literal["fetch_target_data", "failure"]:
    return "failure" if _has_error(state) else "fetch_target_data"


def route_after_search(state: PropertyAnalysisState) -> Literal["hard_filter", "failure"]:
    return "failure" if _has_error(state) else "hard_filter"


def route_after_evidence_evaluation(
    state: PropertyAnalysisState,
) -> Literal["expand_search", "retrieve_supporting_evidence"]:
    """Widen the search, or move on with what we have.

    Expansion is only worth attempting while the budget allows AND the search
    window can still grow — widening a radius that is already at its maximum
    would burn a retry for nothing.
    """
    settings = get_settings()
    quality = EvidenceQuality(state.get("evidence_quality", "insufficient"))
    included = sum(1 for item in state.get("reranked_comps") or [] if item.included)

    at_max_window = (
        state.get("search_radius_km", 0) >= settings.max_radius_km
        and state.get("lookback_months", 0) >= settings.max_lookback_months
    )
    budget_left = state.get("expansions_used", 0) < settings.max_search_expansions
    needs_more = not quality.is_sufficient or included < settings.min_strong_comparables

    if needs_more and budget_left and not at_max_window:
        return "expand_search"
    return "retrieve_supporting_evidence"


def route_after_evidence_retrieval(
    state: PropertyAnalysisState,
) -> Literal["research_more", "assess_price"]:
    settings = get_settings()
    bundle = state.get("evidence")
    sufficient = bool(bundle and bundle.is_sufficient)
    budget_left = state.get("evidence_retries", 0) < settings.max_evidence_retries
    if not sufficient and budget_left:
        return "research_more"
    return "assess_price"


def route_after_assessment(state: PropertyAnalysisState) -> Literal["guardrail_check", "failure"]:
    return "failure" if _has_error(state) else "guardrail_check"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------
def build_graph(checkpointer: Any | None = None) -> Any:
    graph = StateGraph(PropertyAnalysisState)

    graph.add_node("resolve_property", nodes.resolve_property_node)
    graph.add_node("fetch_target_data", nodes.fetch_target_data_node)
    graph.add_node("search_sold_candidates", nodes.search_sold_candidates_node)
    graph.add_node("hard_filter", nodes.hard_filter_node)
    graph.add_node("hybrid_retrieval", nodes.hybrid_retrieval_node)
    graph.add_node("rerank", nodes.rerank_node)
    graph.add_node("evaluate_evidence", nodes.evaluate_evidence_node)
    graph.add_node("expand_search", nodes.expand_search_node)
    graph.add_node("retrieve_supporting_evidence", nodes.retrieve_evidence_node)
    graph.add_node("research_more", nodes.research_more_node)
    graph.add_node("assess_price", nodes.assess_price_node)
    graph.add_node("guardrail_check", nodes.guardrail_node)
    graph.add_node("finalise", nodes.finalise_node)
    graph.add_node("failure", nodes.failure_node)

    graph.add_edge(START, "resolve_property")
    graph.add_conditional_edges("resolve_property", route_after_resolve)
    graph.add_edge("fetch_target_data", "search_sold_candidates")
    graph.add_conditional_edges("search_sold_candidates", route_after_search)
    graph.add_edge("hard_filter", "hybrid_retrieval")
    graph.add_edge("hybrid_retrieval", "rerank")
    graph.add_edge("rerank", "evaluate_evidence")
    graph.add_conditional_edges("evaluate_evidence", route_after_evidence_evaluation)
    # The expansion loop returns to the sold-sales search, so a wider radius
    # actually pulls new data rather than re-filtering the same rows.
    graph.add_edge("expand_search", "search_sold_candidates")
    graph.add_conditional_edges("retrieve_supporting_evidence", route_after_evidence_retrieval)
    graph.add_edge("research_more", "retrieve_supporting_evidence")
    graph.add_conditional_edges("assess_price", route_after_assessment)
    graph.add_edge("guardrail_check", "finalise")
    graph.add_edge("finalise", END)
    graph.add_edge("failure", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())


_COMPILED: Any | None = None


def get_graph() -> Any:
    global _COMPILED
    if _COMPILED is None:
        configure_tracing()
        _COMPILED = build_graph()
    return _COMPILED


async def run_analysis(
    *,
    analysis_id: str,
    query: str,
    asking_price_override: int | None = None,
    user_excluded_external_ids: list[str] | None = None,
    radius_km: float | None = None,
    lookback_months: int | None = None,
    tracer: TraceRecorder | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[PropertyAnalysisState, TraceRecorder, int]:
    """Execute one analysis. Returns `(final_state, tracer, duration_ms)`."""
    settings = get_settings()
    tracer = tracer or TraceRecorder()
    nodes.register_tracer(analysis_id, tracer)

    state = initial_state(
        analysis_id=analysis_id,
        query=query,
        radius_km=radius_km or settings.default_radius_km,
        lookback_months=lookback_months or settings.default_lookback_months,
        asking_price_override=asking_price_override,
        user_excluded_external_ids=user_excluded_external_ids,
    )
    config = {
        "configurable": {"thread_id": analysis_id},
        # Bounded: 13 nodes plus the worst-case loop iterations, with headroom.
        "recursion_limit": 60,
        "run_name": "property-price-analysis",
        "metadata": {"analysis_id": analysis_id, "query": query},
    }
    started = time.perf_counter()
    try:
        # Streaming rather than `ainvoke` so the caller can persist progress as each
        # node completes. The UI shows real stages as they happen instead of a
        # spinner followed by everything at once.
        final: PropertyAnalysisState = state
        async for snapshot in get_graph().astream(state, config=config, stream_mode="values"):
            final = snapshot  # type: ignore[assignment]
            if on_progress is not None:
                try:
                    await on_progress(snapshot)
                except Exception:
                    logger.warning("agent.progress_callback_failed", analysis_id=analysis_id)
    except Exception as exc:
        logger.exception("agent.run_failed", analysis_id=analysis_id)
        tracer.record("node", "graph", status="error", error=f"{type(exc).__name__}: {exc}")
        state["error"] = f"{type(exc).__name__}: {exc}"
        state["failed_stage"] = "graph"
        # Nodes deliberately take and return plain dicts (LangGraph passes partial
        # updates), so merging one back into the TypedDict is unchecked by nature.
        merged = {**state, **(await nodes.failure_node(dict(state)))}
        final = cast(PropertyAnalysisState, merged)
    finally:
        nodes.release_tracer(analysis_id)

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "agent.run_complete",
        analysis_id=analysis_id,
        duration_ms=duration_ms,
        stages=len(final.get("stages", [])),
        tool_calls=len(tracer.tool_calls()),
        expansions=final.get("expansions_used", 0),
        evidence_retries=final.get("evidence_retries", 0),
    )
    return final, tracer, duration_ms  # type: ignore[return-value]
