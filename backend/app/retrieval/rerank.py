"""STAGE E — reranking the shortlist down to the strongest 5-10 comparables.

Retrieval optimises for *recall*: cast a wide net, tolerate some noise. The final
comparable set needs *precision*, because every property in it moves the evidence
range the user will negotiate against. Those are different objectives and they
need different machinery — hence a separate reranking stage rather than simply
taking the top-N of the fused ranking.

TWO GRADERS, ONE CONTRACT
-------------------------
Both emit `RerankVerdict`, so nothing downstream knows or cares which ran.

* `LLMRelevanceGrader` — an LLM with structured output, judging comparability the
  way a valuer would: type, location, recency, size, and the qualitative
  differences that structured fields cannot express ("renovated vs original
  condition" is worth more than a 3 m² floor-area gap).

* `DeterministicRelevanceGrader` — rules over the same inputs, used when no LLM
  credentials exist. Weaker at qualitative nuance, and honest about it.

THE CRITICAL GUARDRAIL
----------------------
The grader receives a fact sheet built from the database and is permitted only to
*judge* it. `_verify_no_invention()` rejects any verdict that introduces a dollar
figure, an address or a numeric attribute absent from the input. A model that
hallucinates a comparable sale in a price analysis is not a quality problem, it
is a safety problem.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.llm import LLMUnavailable, llm_backend_name, llm_is_available, structured_call
from app.observability import TraceRecorder, get_logger
from app.retrieval.concepts import CONCEPTS_BY_KEY
from app.retrieval.hybrid import FusedCandidate
from app.schemas.assessment import RerankVerdict
from app.schemas.property import PropertyRecord

logger = get_logger(__name__)

MAX_CONCURRENT_GRADES = 6

SYSTEM_PROMPT = """You are assisting a buyer's advocate in Australia by judging whether a \
recently SOLD property is a defensible comparable sale for a TARGET property.

You will be given a fact sheet. Rules you must follow:

1. Use ONLY the facts provided. Never introduce a property, address, price, date, \
size or feature that is not in the fact sheet.
2. If a fact is marked "not recorded", treat it as unknown. Do not guess it, and \
mention it as a difference only if the gap genuinely matters.
3. Judge comparability the way a valuer would, weighting in this order: \
property type, location/distance, recency of sale, size and configuration, then \
qualitative condition and features.
4. A large qualitative gap (renovated vs original condition; quiet street vs main \
road; parking vs none) matters more than a small numeric gap.
5. similarity_score: 1.0 = near-identical substitute; 0.7 = solid comparable with \
explainable differences; 0.4 = weak, use with caution; below 0.3 = not comparable.
6. Set comparable=false when a buyer would not accept this sale as evidence about \
the target's value.
7. important_matches and important_differences must be short factual phrases drawn \
from the fact sheet. Do not speculate about price impact in dollars."""


@dataclass
class RerankedCandidate:
    fused: FusedCandidate
    verdict: RerankVerdict
    final_rank: int | None = None
    included: bool = False
    exclusion_reason: str | None = None
    graded_by: str = "unknown"

    @property
    def property_id(self):
        return self.fused.property_id

    @property
    def sold_price(self) -> int:
        return self.fused.candidate.sold_price


def _fact_sheet(target: PropertyRecord, target_text: str, fused: FusedCandidate) -> str:
    """Everything the grader is allowed to know, and nothing else."""
    candidate = fused.candidate

    def show(value: object, suffix: str = "") -> str:
        return f"{value}{suffix}" if value is not None else "not recorded"

    target_block = "\n".join(
        [
            "TARGET PROPERTY (currently for sale)",
            f"  Address       : {target.address}",
            f"  Type          : {target.property_type.value}",
            f"  Bedrooms      : {show(target.bedrooms)}",
            f"  Bathrooms     : {show(target.bathrooms)}",
            f"  Car spaces    : {show(target.carspaces)}",
            f"  Internal area : {show(target.floor_area_sqm, ' m2')}",
            f"  Land area     : {show(target.land_area_sqm, ' m2')}",
            f"  Listing text  : {target_text[:900]}",
        ]
    )
    candidate_block = "\n".join(
        [
            "CANDIDATE COMPARABLE (already sold)",
            f"  Address       : {candidate.address}",
            f"  Type          : {candidate.property_type}",
            f"  Bedrooms      : {show(candidate.bedrooms)}",
            f"  Bathrooms     : {show(candidate.bathrooms)}",
            f"  Car spaces    : {show(candidate.carspaces)}",
            f"  Internal area : {show(candidate.floor_area, ' m2')}",
            f"  Land area     : {show(candidate.land_area, ' m2')}",
            f"  Sold price    : ${candidate.sold_price:,}",
            f"  Sold date     : {candidate.sold_at} ({candidate.months_ago:.1f} months ago)",
            f"  Distance      : {candidate.distance_km:.2f} km from the target",
            f"  Listing text  : {(candidate.description or 'not recorded')[:900]}",
        ]
    )
    retrieval_block = "\n".join(
        [
            "RETRIEVAL SIGNALS (context only; do not treat as facts about the property)",
            f"  Structural closeness : {fused.structural_score:.3f}",
            "  Semantic similarity  : "
            + (f"{fused.vector_similarity:.3f}" if fused.vector_similarity is not None else "n/a"),
            "  Shared features      : "
            + (
                ", ".join(
                    CONCEPTS_BY_KEY[key].label
                    for key in fused.shared_concepts
                    if key in CONCEPTS_BY_KEY
                )
                or "none detected"
            ),
        ]
    )
    return f"{target_block}\n\n{candidate_block}\n\n{retrieval_block}"


_MONEY = re.compile(r"\$\s?\d[\d,\.]*\s?(k|m|million|thousand)?", re.IGNORECASE)
_NUMBER = re.compile(r"\b\d[\d,\.]*\b")


def _verify_no_invention(
    verdict: RerankVerdict, fact_sheet: str
) -> tuple[RerankVerdict, list[str]]:
    """Strip any claim containing a figure that was not in the fact sheet."""
    notes: list[str] = []
    permitted = set(_NUMBER.findall(fact_sheet.replace(",", "")))
    permitted |= set(_NUMBER.findall(fact_sheet))

    def clean(items: list[str], label: str) -> list[str]:
        kept: list[str] = []
        for item in items:
            invented = [
                number
                for number in _NUMBER.findall(item)
                if number not in permitted and number.replace(",", "") not in permitted
            ]
            if invented or (
                _MONEY.search(item) and not any(m in fact_sheet for m in _MONEY.findall(item))
            ):
                notes.append(f"Dropped a {label} containing unsupported figures: {item[:80]!r}")
                continue
            kept.append(item)
        return kept

    cleaned = verdict.model_copy(
        update={
            "important_matches": clean(verdict.important_matches, "match"),
            "important_differences": clean(verdict.important_differences, "difference"),
        }
    )
    return cleaned, notes


class LLMRelevanceGrader:
    """Structured-output grader backed by a hosted LLM."""

    name = "llm_structured_grader"

    async def grade(
        self, target: PropertyRecord, target_text: str, fused: FusedCandidate
    ) -> tuple[RerankVerdict, list[str]]:
        fact_sheet = _fact_sheet(target, target_text, fused)
        verdict = await structured_call(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=fact_sheet)],
            RerankVerdict,
            name="rerank_grade",
        )
        return _verify_no_invention(verdict, fact_sheet)


class DeterministicRelevanceGrader:
    """Rule-based grader used when no LLM backend is configured.

    Transparent by construction, and weaker than an LLM at qualitative nuance —
    which is why `generated_by` records that this ran.
    """

    name = "deterministic_grader"

    def _difference_phrases(
        self, target: PropertyRecord, fused: FusedCandidate
    ) -> tuple[list[str], list[str], float]:
        candidate = fused.candidate
        matches: list[str] = []
        differences: list[str] = []
        penalty = 0.0

        if target.bedrooms is not None and candidate.bedrooms is not None:
            gap = candidate.bedrooms - target.bedrooms
            if gap == 0:
                matches.append(f"Same bedroom count ({candidate.bedrooms})")
            else:
                differences.append(
                    f"{abs(gap)} {'more' if gap > 0 else 'fewer'} bedroom{'s' if abs(gap) != 1 else ''}"
                )
                penalty += 0.18 * abs(gap)
        if target.bathrooms is not None and candidate.bathrooms is not None:
            gap = candidate.bathrooms - target.bathrooms
            if gap == 0:
                matches.append(f"Same bathroom count ({candidate.bathrooms})")
            else:
                differences.append(
                    f"{abs(gap)} {'more' if gap > 0 else 'fewer'} bathroom{'s' if abs(gap) != 1 else ''}"
                )
                penalty += 0.08 * abs(gap)
        if target.carspaces is not None and candidate.carspaces is not None:
            if target.carspaces == candidate.carspaces:
                matches.append(f"Same parking provision ({candidate.carspaces})")
            else:
                differences.append(
                    f"{candidate.carspaces} car space(s) versus {target.carspaces} at the target"
                )
                penalty += 0.06
        if target.floor_area_sqm and candidate.floor_area:
            ratio = (candidate.floor_area - target.floor_area_sqm) / target.floor_area_sqm
            if abs(ratio) <= 0.08:
                matches.append(f"Similar internal area ({candidate.floor_area:g} m²)")
            else:
                differences.append(
                    f"Internal area {abs(ratio):.0%} {'larger' if ratio > 0 else 'smaller'} "
                    f"({candidate.floor_area:g} m² vs {target.floor_area_sqm:g} m²)"
                )
                penalty += min(0.20, abs(ratio) * 0.5)

        for key in fused.shared_concepts:
            concept = CONCEPTS_BY_KEY.get(key)
            if concept:
                matches.append(f"Both describe {concept.label.lower()}")
        for key, register in fused.differing_concepts.items():
            bare = key.removeprefix("missing:")
            concept = CONCEPTS_BY_KEY.get(bare)
            if not concept:
                continue
            if register == "absent":
                differences.append(f"No mention of {concept.label.lower()} in the sold listing")
                penalty += 0.04
            else:
                differences.append(
                    f"Sold listing describes {concept.label.lower()}; target does not"
                )
                penalty += 0.06 if concept.direction == "negative" else 0.03

        if candidate.months_ago > 12:
            differences.append(f"Sold {candidate.months_ago:.0f} months ago")
            penalty += 0.10
        if candidate.distance_km > 2.0:
            differences.append(f"{candidate.distance_km:.1f} km from the target")
            penalty += 0.06
        return matches[:8], differences[:8], penalty

    async def grade(
        self, target: PropertyRecord, target_text: str, fused: FusedCandidate
    ) -> tuple[RerankVerdict, list[str]]:
        matches, differences, penalty = self._difference_phrases(target, fused)
        base = 0.55 * fused.structural_score + 0.45 * (fused.vector_similarity or 0.0)
        score = max(0.0, min(1.0, base * 1.25 - penalty))
        return (
            RerankVerdict(
                comparable=score >= 0.35,
                similarity_score=round(score, 4),
                important_matches=matches,
                important_differences=differences,
                reason=(
                    "Deterministic grade from structural closeness, semantic similarity and "
                    "detected feature overlap. No language model was used."
                ),
            ),
            [],
        )


def get_grader() -> LLMRelevanceGrader | DeterministicRelevanceGrader:
    return LLMRelevanceGrader() if llm_is_available() else DeterministicRelevanceGrader()


@dataclass
class RerankResult:
    ranked: list[RerankedCandidate] = field(default_factory=list)
    graded_by: str = "unknown"
    guardrail_notes: list[str] = field(default_factory=list)

    @property
    def included(self) -> list[RerankedCandidate]:
        return [item for item in self.ranked if item.included]


async def rerank_candidates(
    *,
    target: PropertyRecord,
    target_text: str,
    fused: list[FusedCandidate],
    max_included: int | None = None,
    min_similarity: float = 0.35,
    tracer: TraceRecorder | None = None,
) -> RerankResult:
    """Grade every shortlisted candidate, then select the strongest set."""
    settings = get_settings()
    max_included = max_included or settings.target_comparables
    grader = get_grader()
    guardrail_notes: list[str] = []

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_GRADES)
    fallback = DeterministicRelevanceGrader()

    async def grade_one(item: FusedCandidate) -> RerankedCandidate:
        async with semaphore:
            used = grader.name
            try:
                verdict, notes = await grader.grade(target, target_text, item)
            except (LLMUnavailable, Exception) as exc:
                # One bad grade must not sink the whole analysis; fall back for
                # this candidate only and record that it happened.
                logger.warning(
                    "rerank.grade_failed",
                    candidate=item.candidate.external_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                guardrail_notes.append(
                    f"Grading fell back to deterministic rules for {item.candidate.address} "
                    f"({type(exc).__name__})."
                )
                verdict, notes = await fallback.grade(target, target_text, item)
                used = fallback.name
            guardrail_notes.extend(notes)
            return RerankedCandidate(fused=item, verdict=verdict, graded_by=used)

    graded = await asyncio.gather(*(grade_one(item) for item in fused))
    graded.sort(key=lambda item: item.verdict.similarity_score, reverse=True)

    included_count = 0
    for rank, item in enumerate(graded, start=1):
        item.final_rank = rank
        if not item.verdict.comparable:
            item.exclusion_reason = "Grader judged this not comparable."
        elif item.verdict.similarity_score < min_similarity:
            item.exclusion_reason = (
                f"Comparability {item.verdict.similarity_score:.2f} below the "
                f"{min_similarity:.2f} inclusion threshold."
            )
        elif included_count >= max_included:
            item.exclusion_reason = f"Outside the strongest {max_included} comparables."
        else:
            item.included = True
            included_count += 1

    if tracer:
        tracer.record(
            "rerank",
            grader.name,
            graded=len(graded),
            included=included_count,
            backend=llm_backend_name(),
            top_score=graded[0].verdict.similarity_score if graded else None,
        )
    logger.info(
        "retrieval.rerank",
        grader=grader.name,
        graded=len(graded),
        included=included_count,
        guardrail_notes=len(guardrail_notes),
    )
    return RerankResult(ranked=graded, graded_by=grader.name, guardrail_notes=guardrail_notes)
