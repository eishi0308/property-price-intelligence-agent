"""Retrieval evaluation: does each stage actually earn its place?

Five configurations are measured on identical Stage-A candidate sets, so the only
variable is the ranking method:

    structural_only   deterministic closeness (the no-AI baseline)
    keyword_only      PostgreSQL full-text ranking
    semantic_only     pgvector cosine similarity
    hybrid_rrf        reciprocal rank fusion of all three
    hybrid_rerank     fusion followed by the grader, truncated to the final set

The baseline is the point. If `hybrid_rrf` cannot beat `structural_only`, the
embedding infrastructure is decoration and the honest response is to remove it —
which is exactly the question this suite exists to answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.db import repository
from app.db.engine import session_scope
from app.observability import get_logger
from app.providers.demo import DemoProvider
from app.retrieval.embeddings import embedding_model_name, get_embeddings
from app.retrieval.hybrid import hybrid_retrieve, single_arm_ranking
from app.retrieval.rerank import rerank_candidates
from app.retrieval.semantic_text import build_query_document
from app.retrieval.sql_filter import criteria_for, hard_filter
from evals.metrics import RankingScores, average_scores, score_ranking

logger = get_logger(__name__)

GOLDEN = Path(__file__).resolve().parent / "golden" / "comparables.json"
CONFIGURATIONS = ("structural_only", "keyword_only", "semantic_only", "hybrid_rrf", "hybrid_rerank")


@dataclass
class CaseResult:
    target: str
    relevant_total: int
    near_miss_total: int
    scores: dict[str, RankingScores] = field(default_factory=dict)
    skipped_reason: str | None = None


@dataclass
class RetrievalEvalResult:
    cases: list[CaseResult] = field(default_factory=list)
    by_configuration: dict[str, dict[str, float | int]] = field(default_factory=dict)
    embedding_model: str = ""
    graded_by: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def graded_cases(self) -> list[CaseResult]:
        return [case for case in self.cases if case.skipped_reason is None]


async def evaluate_retrieval(
    radius_km: float = 3.0, lookback_months: int = 12
) -> RetrievalEvalResult:
    dataset = json.loads(GOLDEN.read_text(encoding="utf-8"))
    provider = DemoProvider()
    result = RetrievalEvalResult(embedding_model=embedding_model_name())
    result.notes.append(dataset["_meta"]["corpus"])

    per_configuration: dict[str, list[RankingScores]] = {name: [] for name in CONFIGURATIONS}

    for case in dataset["cases"]:
        relevant = set(case["relevant_ids"])
        case_result = CaseResult(
            target=case["target_address"],
            relevant_total=len(relevant),
            near_miss_total=case["near_miss_count"],
        )
        if not relevant:
            case_result.skipped_reason = (
                "No comparable in the corpus meets the golden criteria for this target, so "
                "precision and recall are undefined. Reported, not silently dropped."
            )
            result.cases.append(case_result)
            continue

        resolution = await provider.resolve_property(case["target_query"])
        target = resolution.property_record
        if target is None:
            case_result.skipped_reason = "Target could not be resolved."
            result.cases.append(case_result)
            continue
        # Held-out targets are sold properties: their qualitative text lives on the
        # historical listing, not a current one. Using the degraded attributes-only
        # document would silently handicap the semantic arm and invalidate the
        # comparison.
        listing = await provider.get_current_listing(target.external_id)
        if listing is None:
            listing = await provider.get_historical_listing(target.external_id)
        document = build_query_document(target, listing)
        embedding = await get_embeddings().aembed_query(document.text)

        async with session_scope() as session:
            row = await repository.get_property_by_external_id(session, "demo", target.external_id)
            criteria = criteria_for(
                target,
                radius_km=radius_km,
                lookback_months=lookback_months,
                exclude_property_ids=frozenset({row.id}) if row else frozenset(),
                limit=200,
            )
            filtered = await hard_filter(session, criteria)
            fused = await hybrid_retrieve(
                session,
                target=target,
                target_document=document,
                target_embedding=embedding,
                candidates=filtered.candidates,
                radius_km=radius_km,
                limit=len(filtered.candidates),  # rank everything so recall is measurable
            )

        rankings: dict[str, list[str]] = {
            "structural_only": [
                item.candidate.external_id for item in single_arm_ranking(fused, "structural")
            ],
            "keyword_only": [
                item.candidate.external_id for item in single_arm_ranking(fused, "keyword")
            ],
            "semantic_only": [
                item.candidate.external_id for item in single_arm_ranking(fused, "semantic")
            ],
            "hybrid_rrf": [item.candidate.external_id for item in fused],
        }

        reranked = await rerank_candidates(
            target=target,
            target_text=document.text,
            fused=fused[:24],
            max_included=10,
        )
        result.graded_by = reranked.graded_by
        rankings["hybrid_rerank"] = [
            item.fused.candidate.external_id
            for item in sorted(reranked.ranked, key=lambda x: x.final_rank or 999)
        ]

        for name, ranking in rankings.items():
            scores = score_ranking(ranking, relevant)
            case_result.scores[name] = scores
            per_configuration[name].append(scores)
        result.cases.append(case_result)

    result.by_configuration = {
        name: average_scores(scores) for name, scores in per_configuration.items()
    }
    return result


def render_retrieval(result: RetrievalEvalResult) -> str:
    lines = ["", "RETRIEVAL EVALUATION", "=" * 78, ""]
    lines.append(f"Embedding model : {result.embedding_model}")
    lines.append(f"Reranked by     : {result.graded_by or 'n/a'}")
    lines.append(f"Graded cases    : {len(result.graded_cases)} of {len(result.cases)}")
    lines.append(f"Relevance labels: {sum(case.relevant_total for case in result.graded_cases)}")
    lines.append("")
    header = (
        f"{'configuration':<18}{'P@5':>7}{'P@10':>7}{'R@5':>7}{'R@10':>7}{'MRR':>7}{'nDCG@10':>9}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for name in CONFIGURATIONS:
        row = result.by_configuration.get(name, {})
        lines.append(
            f"{name:<18}{row.get('P@5', 0):>7}{row.get('P@10', 0):>7}"
            f"{row.get('R@5', 0):>7}{row.get('R@10', 0):>7}"
            f"{row.get('MRR', 0):>7}{row.get('nDCG@10', 0):>9}"
        )
    lines.append("")

    def ndcg(name: str) -> float:
        return float(result.by_configuration.get(name, {}).get("nDCG@10", 0) or 0)

    baseline = ndcg("structural_only")
    hybrid = ndcg("hybrid_rrf")
    reranked = ndcg("hybrid_rerank")
    single_arms = {
        name: ndcg(name) for name in ("structural_only", "keyword_only", "semantic_only")
    }
    best_arm_name = max(single_arms, key=lambda key: single_arms[key])
    best_arm = single_arms[best_arm_name]

    lines.append("VERDICT")
    lines.append(
        f"  vs no-AI structural baseline : hybrid {hybrid:.3f} "
        f"{'>' if hybrid > baseline else '<='} baseline {baseline:.3f}"
    )
    lines.append(
        f"  vs best single arm           : hybrid {hybrid:.3f} "
        f"{'>' if hybrid > best_arm else '<='} {best_arm_name} {best_arm:.3f}"
    )
    if hybrid > best_arm:
        lines.append(
            "  Fusion beats every individual arm, which is the bar that justifies running "
            "three retrievers instead of one."
        )
    else:
        lines.append(
            f"  Fusion does NOT beat '{best_arm_name}' on this corpus. That is a real finding, "
            f"not a rounding error: the weakest arm is dragging the fusion down. The remedy is "
            f"to weight the arms by measured quality (see EVALUATION.md), not to keep an "
            f"under-performing arm because the architecture diagram looks better with it."
        )
    if reranked > hybrid:
        lines.append(f"  Reranking further improves nDCG@10 to {reranked:.3f}.")
    elif reranked:
        lines.append(
            f"  Reranking did not improve nDCG@10 ({reranked:.3f} vs {hybrid:.3f}); with the "
            f"deterministic grader this is expected, since it re-weights signals the fusion "
            f"already used rather than adding qualitative judgement. An LLM grader is the "
            f"component that changes this."
        )
    lines.append("")
    lines.append("PER CASE (first 8)")
    for case in result.cases[:8]:
        if case.skipped_reason:
            lines.append(f"  {case.target[:50]:<52} SKIPPED — {case.skipped_reason[:70]}")
            continue
        lines.append(
            f"  {case.target[:50]:<52} relevant={case.relevant_total:<3} "
            f"near-miss={case.near_miss_total:<3}"
        )
        for name in CONFIGURATIONS:
            scores = case.scores.get(name)
            if scores:
                row = scores.as_row()
                lines.append(
                    f"      {name:<18} P@5={row['P@5']:<6} R@10={row['R@10']:<6} "
                    f"MRR={row['MRR']:<6} nDCG@10={row['nDCG@10']}"
                )
    for note in result.notes:
        lines.append(f"\n  NOTE: {note}")
    return "\n".join(lines)
