"""Orchestration between the HTTP API and the agent.

Responsibilities kept here rather than in the routes or the graph:

* creating and updating the `analyses` row, so an in-flight analysis is visible
  and pollable rather than trapped inside a coroutine;
* persisting comparables, evidence and traces with their full provenance;
* projecting database rows into the API's response schemas;
* the human-in-the-loop re-run, which must preserve the user's exclusions across
  a fresh agent run.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime
from functools import lru_cache
from typing import Any

from app.agent.graph import run_analysis
from app.agent.state import PropertyAnalysisState
from app.config import get_settings
from app.db import repository
from app.db.engine import session_scope
from app.db.models import ComparableResult, Property
from app.llm import llm_backend_name
from app.mcp_servers import get_toolset
from app.observability import TraceRecorder, get_logger
from app.retrieval.embeddings import embedding_model_name, embedding_quality_note
from app.schemas.analysis import (
    AnalysisDetail,
    AnalysisStatus,
    AnalysisSummary,
    ComparableView,
    EvidenceView,
    RunMetadata,
    StageProgress,
    TraceEvent,
)
from app.schemas.assessment import EvidenceQuality, PriceAssessment
from app.schemas.property import Coordinates, PropertyRecord, PropertyType

logger = get_logger(__name__)


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


class AnalysisService:
    """Runs analyses and serves their results."""

    def __init__(self) -> None:
        self._running: dict[str, asyncio.Task[None]] = {}

    # -- creation ----------------------------------------------------------
    async def create(self, query: str, asking_price_override: int | None = None) -> uuid.UUID:
        async with session_scope() as session:
            analysis = await repository.create_analysis(session, query=query)
            analysis_id = analysis.id
        task = asyncio.create_task(
            self._execute(analysis_id, query, asking_price_override, []),
            name=f"analysis-{analysis_id}",
        )
        self._running[str(analysis_id)] = task
        task.add_done_callback(lambda _: self._running.pop(str(analysis_id), None))
        return analysis_id

    async def rerun(self, analysis_id: uuid.UUID) -> bool:
        """Re-run an existing analysis, honouring the user's exclusions."""
        async with session_scope() as session:
            analysis = await repository.get_analysis(session, analysis_id)
            if analysis is None:
                return False
            comparables = await repository.get_comparables(session, analysis_id)
            excluded_ids = [
                item.comparable_property_id for item in comparables if item.excluded_by_user
            ]
            properties = await repository.get_properties(session, excluded_ids)
            excluded_external = [row.external_id for row in properties.values()]
            query = analysis.query
            asking_price = (
                analysis.asking_price if analysis.asking_price_source == "user_supplied" else None
            )
            await repository.update_analysis(session, analysis_id, status="running", error=None)

        task = asyncio.create_task(
            self._execute(analysis_id, query, asking_price, excluded_external),
            name=f"analysis-rerun-{analysis_id}",
        )
        self._running[str(analysis_id)] = task
        task.add_done_callback(lambda _: self._running.pop(str(analysis_id), None))
        return True

    async def wait_for(self, analysis_id: uuid.UUID, timeout: float = 120.0) -> None:
        """Block until a running analysis finishes (used by tests and sync clients)."""
        task = self._running.get(str(analysis_id))
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    # -- execution ---------------------------------------------------------
    async def _execute(
        self,
        analysis_id: uuid.UUID,
        query: str,
        asking_price_override: int | None,
        excluded_external_ids: list[str],
    ) -> None:
        settings = get_settings()
        tracer = TraceRecorder()
        async with session_scope() as session:
            await repository.update_analysis(session, analysis_id, status="running")

        async def on_progress(snapshot: PropertyAnalysisState) -> None:
            stages = snapshot.get("stages") or []
            if not stages:
                return
            async with session_scope() as session:
                await repository.update_analysis(
                    session,
                    analysis_id,
                    stages={"items": stages},
                    search_radius_km=snapshot.get("search_radius_km"),
                    lookback_months=snapshot.get("lookback_months"),
                )

        try:
            final, tracer, duration_ms = await run_analysis(
                analysis_id=str(analysis_id),
                query=query,
                asking_price_override=asking_price_override,
                user_excluded_external_ids=excluded_external_ids,
                tracer=tracer,
                on_progress=on_progress,
            )
        except Exception as exc:
            logger.exception("analysis.failed", analysis_id=str(analysis_id))
            async with session_scope() as session:
                await repository.update_analysis(
                    session, analysis_id, status="failed", error=f"{type(exc).__name__}: {exc}"
                )
            return

        await self._persist(analysis_id, final, tracer, duration_ms, settings)

    async def _persist(
        self,
        analysis_id: uuid.UUID,
        final: PropertyAnalysisState,
        tracer: TraceRecorder,
        duration_ms: int,
        settings: Any,
    ) -> None:
        assessment: PriceAssessment | None = final.get("assessment")
        target: PropertyRecord | None = final.get("target_property")
        toolset = await get_toolset()

        metadata = RunMetadata(
            provider=settings.property_provider.value,
            provider_is_demo=bool(target.is_demo_data)
            if target
            else settings.property_provider.value == "demo",
            llm_backend=llm_backend_name(settings),
            embedding_backend=settings.resolved_embedding_backend.value,
            embedding_model=embedding_model_name(settings),
            tracing_enabled=settings.tracing_enabled,
            vector_retrieval_quality=embedding_quality_note(settings),
            search_radius_km=final.get("search_radius_km", settings.default_radius_km),
            lookback_months=final.get("lookback_months", settings.default_lookback_months),
            expansions_used=final.get("expansions_used", 0),
            duration_ms=duration_ms,
            mcp_transport=toolset.transport,
            mcp_degraded_reason=toolset.degraded_reason,
            tool_calls=final.get("tool_history", []),
        ).model_dump(mode="json")

        async with session_scope() as session:
            await repository.update_analysis(
                session,
                analysis_id,
                property_id=uuid.UUID(final["target_property_db_id"])
                if final.get("target_property_db_id")
                else None,
                status="failed" if final.get("error") and not assessment else "complete",
                asking_price=final.get("asking_price"),
                asking_price_source=final.get("asking_price_source"),
                evidence_low=assessment.evidence_range_low if assessment else None,
                evidence_high=assessment.evidence_range_high if assessment else None,
                evidence_median=assessment.evidence_median if assessment else None,
                assessment=assessment.assessment.value if assessment else None,
                confidence=assessment.confidence.value if assessment else None,
                evidence_quality=final.get("evidence_quality"),
                assessment_payload=assessment.model_dump(mode="json") if assessment else None,
                run_metadata=metadata,
                stages={"items": final.get("stages", [])},
                missing_information={"items": final.get("missing_information", [])},
                error=final.get("error"),
                search_radius_km=final.get("search_radius_km"),
                lookback_months=final.get("lookback_months"),
                duration_ms=duration_ms,
            )

            rows = []
            for item in final.get("reranked_comps") or []:
                candidate = item.fused.candidate
                rows.append(
                    {
                        "comparable_property_id": candidate.property_id,
                        "structured_match_data": {
                            "bedrooms": candidate.bedrooms,
                            "bathrooms": candidate.bathrooms,
                            "carspaces": candidate.carspaces,
                            "floor_area": candidate.floor_area,
                            "land_area": candidate.land_area,
                            "property_type": candidate.property_type,
                            "structural_score": item.fused.structural_score,
                            "arms_hit": item.fused.arms_hit(),
                            "graded_by": item.graded_by,
                            "source_url": candidate.source_url,
                            "description": (candidate.description or "")[:600],
                            "is_demo_data": candidate.is_demo_data,
                            "source": candidate.source,
                            "suburb": candidate.suburb,
                            "address": candidate.address,
                            "external_id": candidate.external_id,
                        },
                        "vector_similarity": item.fused.vector_similarity,
                        "keyword_score": item.fused.keyword_score,
                        "fusion_score": item.fused.fusion_score,
                        "rerank_score": item.verdict.similarity_score,
                        "final_rank": item.final_rank,
                        "included": item.included,
                        "exclusion_reason": item.exclusion_reason,
                        "important_matches": {"items": item.verdict.important_matches},
                        "important_differences": {"items": item.verdict.important_differences},
                        "sold_price": candidate.sold_price,
                        "sold_at": candidate.sold_at,
                        "distance_km": candidate.distance_km,
                    }
                )
            await repository.replace_comparables(session, analysis_id, rows)

            bundle = final.get("evidence")
            evidence_rows = []
            if bundle:
                for evidence_item in bundle.items:
                    evidence_rows.append(
                        {
                            "evidence_chunk_id": evidence_item.chunk_id,
                            "source_type": evidence_item.source_type,
                            "title": f"[{evidence_item.citation_id}] {evidence_item.title}",
                            "content": evidence_item.content,
                            "why_it_matters": evidence_item.why_it_matters,
                            "similarity": evidence_item.similarity,
                            "source": evidence_item.source,
                            "source_url": evidence_item.source_url,
                            "published_at": evidence_item.published_at,
                            "is_demo_data": evidence_item.is_demo_data,
                        }
                    )
            await repository.replace_analysis_evidence(session, analysis_id, evidence_rows)
            await repository.replace_traces(session, analysis_id, tracer.to_dicts())

        logger.info(
            "analysis.persisted",
            analysis_id=str(analysis_id),
            comparables=len(rows),
            evidence=len(evidence_rows),
            duration_ms=duration_ms,
        )

    # -- reads -------------------------------------------------------------
    async def get_detail(self, analysis_id: uuid.UUID) -> AnalysisDetail | None:
        async with session_scope() as session:
            analysis = await repository.get_analysis(session, analysis_id)
            if analysis is None:
                return None
            comparables = await repository.get_comparables(session, analysis_id)
            evidence = await repository.get_analysis_evidence(session, analysis_id)
            target = (
                await repository.get_property(session, analysis.property_id)
                if analysis.property_id
                else None
            )
            target_listing = (
                await repository.current_listing(session, analysis.property_id)
                if analysis.property_id
                else None
            )

        return AnalysisDetail(
            id=str(analysis.id),
            status=AnalysisStatus(analysis.status),
            query=analysis.query,
            created_at=_iso(analysis.created_at) or "",
            target_property=self._to_record(target) if target else None,
            target_description=target_listing.description if target_listing else None,
            asking_price=analysis.asking_price,
            asking_price_source=analysis.asking_price_source,
            assessment=(
                PriceAssessment.model_validate(analysis.assessment_payload)
                if analysis.assessment_payload
                else None
            ),
            comparables=[self._to_comparable_view(item) for item in comparables],
            evidence=[
                EvidenceView(
                    id=str(item.id),
                    source_type=item.source_type,
                    title=item.title or "",
                    content=item.content,
                    why_it_matters=item.why_it_matters or "",
                    source=item.source,
                    source_url=item.source_url,
                    published_at=_iso(item.published_at),
                    similarity=item.similarity,
                    is_demo_data=item.is_demo_data,
                )
                for item in evidence
            ],
            stages=[
                StageProgress.model_validate(row)
                for row in ((analysis.stages or {}).get("items") or [])
            ],
            evidence_quality=(
                EvidenceQuality(analysis.evidence_quality) if analysis.evidence_quality else None
            ),
            missing_information=(analysis.missing_information or {}).get("items", []),
            metadata=RunMetadata.model_validate(analysis.run_metadata)
            if analysis.run_metadata
            else None,
            error=analysis.error,
        )

    async def list_recent(self, limit: int = 20) -> list[AnalysisSummary]:
        async with session_scope() as session:
            rows = await repository.list_analyses(session, limit=limit)
            property_ids = [row.property_id for row in rows if row.property_id]
            properties = await repository.get_properties(session, property_ids)
        return [
            AnalysisSummary(
                id=str(row.id),
                status=AnalysisStatus(row.status),
                query=row.query,
                created_at=_iso(row.created_at) or "",
                address=properties[row.property_id].address
                if row.property_id in properties
                else None,
                assessment=row.assessment,
                confidence=row.confidence,
                asking_price=row.asking_price,
            )
            for row in rows
        ]

    async def get_traces(self, analysis_id: uuid.UUID) -> list[TraceEvent]:
        async with session_scope() as session:
            rows = await repository.get_traces(session, analysis_id)
        return [
            TraceEvent(
                sequence=row.sequence,
                kind=row.kind,
                name=row.name,
                status=row.status,
                duration_ms=row.duration_ms,
                detail=row.detail or {},
                at=_iso(row.created_at),
            )
            for row in rows
        ]

    async def set_exclusion(
        self,
        analysis_id: uuid.UUID,
        comparable_property_id: uuid.UUID,
        *,
        excluded: bool,
        reason: str | None,
    ) -> bool:
        async with session_scope() as session:
            return await repository.set_comparable_exclusion(
                session, analysis_id, comparable_property_id, excluded=excluded, reason=reason
            )

    # -- projections -------------------------------------------------------
    @staticmethod
    def _to_record(row: Property) -> PropertyRecord:
        coordinates = (
            Coordinates(latitude=row.latitude, longitude=row.longitude)
            if row.latitude is not None and row.longitude is not None
            else None
        )
        return PropertyRecord(
            external_id=row.external_id,
            provider=row.provider,
            address=row.address,
            suburb=row.suburb,
            state=row.state,
            postcode=row.postcode,
            coordinates=coordinates,
            property_type=PropertyType(row.property_type),
            bedrooms=row.bedrooms,
            bathrooms=row.bathrooms,
            carspaces=row.carspaces,
            floor_area_sqm=row.floor_area,
            land_area_sqm=row.land_area,
            year_built=row.year_built,
            is_demo_data=row.is_demo_data,
        )

    @staticmethod
    def _to_comparable_view(row: ComparableResult) -> ComparableView:
        data = row.structured_match_data or {}
        return ComparableView(
            id=str(row.comparable_property_id),
            external_id=str(data.get("external_id", "")),
            address=str(data.get("address", "Unknown address")),
            suburb=str(data.get("suburb", "")),
            sold_price=row.sold_price or 0,
            sold_at=_iso(row.sold_at) or "",
            distance_km=row.distance_km,
            bedrooms=data.get("bedrooms"),
            bathrooms=data.get("bathrooms"),
            carspaces=data.get("carspaces"),
            floor_area_sqm=data.get("floor_area"),
            land_area_sqm=data.get("land_area"),
            property_type=str(data.get("property_type", "other")),
            vector_similarity=row.vector_similarity,
            keyword_score=row.keyword_score,
            fusion_score=row.fusion_score,
            rerank_score=row.rerank_score,
            final_rank=row.final_rank,
            important_matches=(row.important_matches or {}).get("items", []),
            important_differences=(row.important_differences or {}).get("items", []),
            included=row.included,
            exclusion_reason=row.exclusion_reason,
            excluded_by_user=row.excluded_by_user,
            source=str(data.get("source", "unknown")),
            source_url=data.get("source_url"),
            description_excerpt=(data.get("description") or None),
            is_demo_data=bool(data.get("is_demo_data", False)),
        )


@lru_cache(maxsize=1)
def get_analysis_service() -> AnalysisService:
    return AnalysisService()
