"""STAGE D — hybrid retrieval by reciprocal rank fusion.

THREE ARMS, THREE DIFFERENT KINDS OF EVIDENCE
---------------------------------------------
1. **Structural** (deterministic SQL/arithmetic) — how close in distance, recency
   and physical attributes. Never wrong, but blind to everything qualitative.
2. **Lexical** (`ts_rank_cd`) — exact terms a buyer cares about: "north-facing",
   "lock-up garage". Precise, but defeated by paraphrase.
3. **Semantic** (pgvector cosine) — meaning regardless of wording. Robust to
   paraphrase, but happy to rank a beautifully-written *dissimilar* property
   highly.

Each arm's blind spot is another arm's strength, which is the only good reason to
run three retrievers instead of one.

WHY RECIPROCAL RANK FUSION
--------------------------
The three arms produce incomparable score scales: `ts_rank_cd` is unbounded and
corpus-dependent, cosine similarity sits in [-1, 1] with a distribution that
shifts with the embedding model, and the structural score is a bounded
hand-built composite. Normalising them onto a common scale would require choosing
weights we have no principled basis for, and would need re-tuning whenever the
embedding backend changes.

RRF discards the magnitudes and keeps only the ordering:

    score(d) = Σ_arms  weight_arm / (k + rank_arm(d))

It is scale-free, needs no tuning, and degrades gracefully when an arm returns
nothing (offline embeddings, or a listing corpus with no text). `k=60` is the
value from Cormack et al. (2009); it damps the influence of the very top ranks so
one confident arm cannot dominate the other two.

Arm weights are configurable but default to equal. `evals/run.py` measures
whether fusion actually beats each arm alone on this corpus — if it does not, the
honest response is to drop an arm, not to keep it for the architecture diagram.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.observability import get_logger
from app.retrieval.concepts import detect_concepts
from app.retrieval.keyword import keyword_search
from app.retrieval.semantic_text import SemanticDocument
from app.retrieval.sql_filter import CandidateRow
from app.retrieval.vector_store import SOURCE_LISTING, similarity_search
from app.schemas.property import PropertyRecord

logger = get_logger(__name__)

DEFAULT_WEIGHTS = {"structural": 1.0, "keyword": 1.0, "semantic": 1.0}


@dataclass
class FusedCandidate:
    """A candidate with every arm's contribution retained for auditability."""

    candidate: CandidateRow
    structural_score: float = 0.0
    structural_rank: int | None = None
    keyword_score: float | None = None
    keyword_rank: int | None = None
    vector_similarity: float | None = None
    vector_rank: int | None = None
    fusion_score: float = 0.0
    shared_concepts: dict[str, str] = field(default_factory=dict)
    differing_concepts: dict[str, str] = field(default_factory=dict)

    @property
    def property_id(self) -> uuid.UUID:
        return self.candidate.property_id

    def arms_hit(self) -> list[str]:
        arms = ["structural"]
        if self.keyword_rank is not None:
            arms.append("keyword")
        if self.vector_rank is not None:
            arms.append("semantic")
        return arms


# ---------------------------------------------------------------------------
# Arm 1 — structural closeness (deterministic; no model of any kind)
# ---------------------------------------------------------------------------
def structural_score(target: PropertyRecord, candidate: CandidateRow, radius_km: float) -> float:
    """Bounded 0-1 closeness on distance, recency and physical attributes.

    Pure arithmetic over observed attributes. It expresses "how alike are these
    two properties on paper", not "what is this worth".
    """
    components: list[tuple[float, float]] = []  # (score, weight)

    proximity = max(0.0, 1.0 - (candidate.distance_km / max(radius_km, 0.1)))
    components.append((proximity, 0.30))

    recency = max(0.0, 1.0 - (candidate.months_ago / 18.0))
    components.append((recency, 0.25))

    if target.bedrooms is not None and candidate.bedrooms is not None:
        components.append((max(0.0, 1.0 - abs(target.bedrooms - candidate.bedrooms) * 0.5), 0.20))
    if target.bathrooms is not None and candidate.bathrooms is not None:
        components.append((max(0.0, 1.0 - abs(target.bathrooms - candidate.bathrooms) * 0.5), 0.10))
    if target.carspaces is not None and candidate.carspaces is not None:
        components.append((max(0.0, 1.0 - abs(target.carspaces - candidate.carspaces) * 0.4), 0.05))
    if target.floor_area_sqm and candidate.floor_area:
        ratio = abs(target.floor_area_sqm - candidate.floor_area) / target.floor_area_sqm
        components.append((max(0.0, 1.0 - ratio * 2.0), 0.10))

    total_weight = sum(weight for _, weight in components)
    if total_weight == 0:
        return 0.0
    return round(sum(score * weight for score, weight in components) / total_weight, 6)


def _rank_map(ordered_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    return {property_id: rank for rank, property_id in enumerate(ordered_ids, start=1)}


async def hybrid_retrieve(
    session: AsyncSession,
    *,
    target: PropertyRecord,
    target_document: SemanticDocument,
    target_embedding: list[float],
    candidates: list[CandidateRow],
    radius_km: float,
    limit: int | None = None,
    weights: dict[str, float] | None = None,
) -> list[FusedCandidate]:
    """Rank Stage-A candidates by fusing three retrieval arms."""
    settings = get_settings()
    limit = limit or settings.hybrid_candidate_limit
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    if not candidates:
        return []

    by_id = {candidate.property_id: candidate for candidate in candidates}
    candidate_ids = list(by_id.keys())

    # --- Arm 1: structural -------------------------------------------------
    structural = {
        property_id: structural_score(target, candidate, radius_km)
        for property_id, candidate in by_id.items()
    }
    structural_ranks = _rank_map(
        sorted(candidate_ids, key=lambda pid: structural[pid], reverse=True)
    )

    # --- Arm 2: lexical ----------------------------------------------------
    keyword_hits = await keyword_search(
        session, target_document.text, property_ids=candidate_ids, k=len(candidate_ids)
    )
    keyword_scores = {hit.property_id: hit.score for hit in keyword_hits}
    keyword_ranks = _rank_map([hit.property_id for hit in keyword_hits])

    # --- Arm 3: semantic ---------------------------------------------------
    vector_hits = await similarity_search(
        session,
        target_embedding,
        source_types=[SOURCE_LISTING],
        property_ids=candidate_ids,
        k=len(candidate_ids),
    )
    vector_scores = {
        hit.property_id: hit.similarity for hit in vector_hits if hit.property_id is not None
    }
    vector_ranks = _rank_map(
        [hit.property_id for hit in vector_hits if hit.property_id is not None]
    )
    vector_concepts = {
        hit.property_id: dict(hit.metadata.get("concepts") or {})
        for hit in vector_hits
        if hit.property_id is not None
    }

    # --- Fusion ------------------------------------------------------------
    k = settings.rrf_k
    fused: list[FusedCandidate] = []
    target_concepts = target_document.concepts
    for property_id, candidate in by_id.items():
        score = 0.0
        if (rank := structural_ranks.get(property_id)) is not None:
            score += weights["structural"] / (k + rank)
        if (rank := keyword_ranks.get(property_id)) is not None:
            score += weights["keyword"] / (k + rank)
        if (rank := vector_ranks.get(property_id)) is not None:
            score += weights["semantic"] / (k + rank)

        candidate_concepts = vector_concepts.get(property_id) or detect_concepts(
            candidate.description or candidate.headline or ""
        )
        shared = {
            key: register for key, register in candidate_concepts.items() if key in target_concepts
        }
        differing = {
            key: register
            for key, register in candidate_concepts.items()
            if key not in target_concepts
        }
        for key in target_concepts:
            if key not in candidate_concepts:
                differing[f"missing:{key}"] = "absent"

        fused.append(
            FusedCandidate(
                candidate=candidate,
                structural_score=structural[property_id],
                structural_rank=structural_ranks.get(property_id),
                keyword_score=keyword_scores.get(property_id),
                keyword_rank=keyword_ranks.get(property_id),
                vector_similarity=vector_scores.get(property_id),
                vector_rank=vector_ranks.get(property_id),
                fusion_score=round(score, 8),
                shared_concepts=shared,
                differing_concepts=differing,
            )
        )

    fused.sort(key=lambda item: (item.fusion_score, item.structural_score), reverse=True)
    logger.info(
        "retrieval.hybrid",
        candidates_in=len(candidates),
        returned=min(limit, len(fused)),
        keyword_arm_hits=len(keyword_hits),
        semantic_arm_hits=len(vector_hits),
        rrf_k=k,
        weights=weights,
    )
    return fused[:limit]


def single_arm_ranking(fused: list[FusedCandidate], arm: str) -> list[FusedCandidate]:
    """Re-order by one arm alone. Used by the evaluation suite for ablations."""
    if arm == "semantic":
        scored = [item for item in fused if item.vector_rank is not None]
        return sorted(scored, key=lambda item: item.vector_similarity or 0.0, reverse=True)
    if arm == "keyword":
        scored = [item for item in fused if item.keyword_rank is not None]
        return sorted(scored, key=lambda item: item.keyword_score or 0.0, reverse=True)
    if arm == "structural":
        return sorted(fused, key=lambda item: item.structural_score, reverse=True)
    raise ValueError(f"Unknown arm: {arm}")
