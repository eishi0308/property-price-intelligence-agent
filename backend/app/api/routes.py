"""HTTP endpoints.

Two conventions worth stating:

* An analysis runs in the background and the client polls. A comparable-sales
  analysis touches an external API, a vector index and (optionally) an LLM; making
  the client hold a request open for that would be a bad trade for a UI that
  wants to show progress anyway.
* Every response that contains an assessment also carries the disclaimer and the
  run metadata (provider, LLM backend, embedding backend, demo flag). Those are
  not optional decoration — a reader must always be able to tell how the answer
  was produced.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.config import get_settings
from app.db.engine import pgvector_available, ping
from app.guardrails import DISCLAIMER
from app.llm import llm_backend_name
from app.mcp_servers import get_toolset
from app.observability import get_logger, tracing_status
from app.providers import get_provider
from app.retrieval.embeddings import embedding_model_name, embedding_quality_note
from app.schemas.analysis import (
    AnalysisCreateRequest,
    AnalysisDetail,
    AnalysisSummary,
    ComparableView,
    DataFeasibilityReport,
    ExcludeComparableRequest,
    TraceEvent,
)
from app.services import get_analysis_service

logger = get_logger(__name__)
router = APIRouter(prefix="/api")


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{label} is not a valid identifier."
        ) from exc


# ---------------------------------------------------------------------------
# Health & capability reporting
# ---------------------------------------------------------------------------
@router.get("/health", tags=["system"])
async def health() -> dict[str, object]:
    """Liveness plus an honest capability report.

    Degraded modes are reported explicitly rather than hidden behind a green
    tick, because every one of them changes how much the output can be trusted.
    """
    settings = get_settings()
    database_up = await ping()
    vector_up = await pgvector_available() if database_up else False
    toolset = await get_toolset()
    provider = get_provider()

    degradations: list[str] = []
    if not database_up:
        degradations.append("PostgreSQL is unreachable; no analysis can run.")
    if database_up and not vector_up:
        degradations.append("pgvector extension is missing; semantic retrieval is unavailable.")
    if provider.capabilities.is_demo:
        degradations.append(
            "Property provider is DEMO: all property data is synthetic fixture data."
        )
    if settings.resolved_llm_backend.value == "offline":
        degradations.append("No LLM configured: reranking and narration use deterministic rules.")
    if settings.resolved_embedding_backend.value == "offline":
        degradations.append(
            "No embedding API configured: semantic similarity uses the offline concept encoder."
        )
    if toolset.degraded_reason:
        degradations.append(toolset.degraded_reason)

    return {
        "status": "ok" if database_up and vector_up else "degraded",
        "database": database_up,
        "pgvector": vector_up,
        "provider": {
            "name": provider.capabilities.name,
            "is_demo": provider.capabilities.is_demo,
            "supports_semantic_retrieval": provider.capabilities.supports_semantic_retrieval,
        },
        "llm_backend": llm_backend_name(settings),
        "embedding": {
            "backend": settings.resolved_embedding_backend.value,
            "model": embedding_model_name(settings),
            "note": embedding_quality_note(settings),
        },
        "mcp": {
            "transport": toolset.transport,
            "servers": toolset.server_names,
            "tools": toolset.names(),
            "degraded_reason": toolset.degraded_reason,
        },
        "tracing": tracing_status(),
        "degradations": degradations,
        "disclaimer": DISCLAIMER,
    }


@router.get("/data-feasibility", response_model=DataFeasibilityReport, tags=["system"])
async def data_feasibility(
    query: str = Query(default="12/45 Redmyre Road, Strathfield NSW 2135"),
) -> DataFeasibilityReport:
    """Run the Phase-1 data gate against the configured provider, live."""
    from scripts.check_property_data import run_feasibility

    provider = get_provider()
    return await run_feasibility(query, provider)


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------
@router.post("/analysis", status_code=status.HTTP_202_ACCEPTED, tags=["analysis"])
async def create_analysis(payload: AnalysisCreateRequest) -> dict[str, str]:
    """Start an analysis. Poll `GET /api/analysis/{id}` for progress and results."""
    service = get_analysis_service()
    analysis_id = await service.create(payload.query, payload.asking_price_override)
    logger.info("api.analysis_created", analysis_id=str(analysis_id), query=payload.query)
    return {"id": str(analysis_id), "status": "running"}


@router.get("/analysis", response_model=list[AnalysisSummary], tags=["analysis"])
async def list_analyses(limit: int = Query(default=20, ge=1, le=100)) -> list[AnalysisSummary]:
    return await get_analysis_service().list_recent(limit=limit)


@router.get("/analysis/{analysis_id}", response_model=AnalysisDetail, tags=["analysis"])
async def get_analysis(analysis_id: str) -> AnalysisDetail:
    detail = await get_analysis_service().get_detail(_parse_uuid(analysis_id, "analysis_id"))
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found.")
    return detail


@router.get(
    "/analysis/{analysis_id}/comparables", response_model=list[ComparableView], tags=["analysis"]
)
async def get_comparables(analysis_id: str) -> list[ComparableView]:
    detail = await get_analysis_service().get_detail(_parse_uuid(analysis_id, "analysis_id"))
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found.")
    return detail.comparables


@router.post(
    "/analysis/{analysis_id}/comparables/{comparable_id}/exclude", tags=["human-in-the-loop"]
)
async def exclude_comparable(
    analysis_id: str, comparable_id: str, payload: ExcludeComparableRequest | None = None
) -> dict[str, object]:
    """Exclude a comparable the user judges inappropriate.

    This records the decision only. The evidence range is not patched in place —
    call `/rerun` so the whole assessment, confidence and narrative are recomputed
    without that sale. Editing the number without redoing the reasoning would
    produce a conclusion no longer supported by its own explanation.
    """
    service = get_analysis_service()
    ok = await service.set_exclusion(
        _parse_uuid(analysis_id, "analysis_id"),
        _parse_uuid(comparable_id, "comparable_id"),
        excluded=True,
        reason=payload.reason if payload else None,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Comparable not found for this analysis."
        )
    return {
        "excluded": True,
        "rerun_required": True,
        "message": "Comparable excluded. Re-run the analysis to recompute the assessment.",
    }


@router.post(
    "/analysis/{analysis_id}/comparables/{comparable_id}/include", tags=["human-in-the-loop"]
)
async def include_comparable(analysis_id: str, comparable_id: str) -> dict[str, object]:
    """Undo a user exclusion."""
    ok = await get_analysis_service().set_exclusion(
        _parse_uuid(analysis_id, "analysis_id"),
        _parse_uuid(comparable_id, "comparable_id"),
        excluded=False,
        reason=None,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Comparable not found for this analysis."
        )
    return {"excluded": False, "rerun_required": True}


@router.post(
    "/analysis/{analysis_id}/rerun",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["human-in-the-loop"],
)
async def rerun_analysis(analysis_id: str) -> dict[str, str]:
    """Re-run the workflow, honouring every user exclusion recorded so far."""
    parsed = _parse_uuid(analysis_id, "analysis_id")
    if not await get_analysis_service().rerun(parsed):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found.")
    return {"id": analysis_id, "status": "running"}


@router.get(
    "/analysis/{analysis_id}/trace", response_model=list[TraceEvent], tags=["observability"]
)
async def get_trace(analysis_id: str) -> list[TraceEvent]:
    """The full local trace: node transitions, tool calls, retrievals, guardrails."""
    return await get_analysis_service().get_traces(_parse_uuid(analysis_id, "analysis_id"))
