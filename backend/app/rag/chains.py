"""LangChain composition for the grounded narrative.

The chain is `prompt | model.with_structured_output(AssessmentNarrative)`. Two
things are deliberately *outside* it:

* the numbers — computed in `app.assessment.pricing` and passed in as facts, so
  the model has nothing to compute and therefore nothing to get wrong;
* the label and confidence — derived from rules, for the same reason.

The model's entire job is to explain, in the buyer's language, what the retrieved
evidence supports. When no LLM is configured, `_offline_narrative` produces the
same `AssessmentNarrative` schema from templates and marks itself as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.assessment.pricing import PriceEvidence
from app.llm import LLMUnavailable, llm_backend_name, llm_is_available
from app.llm.client import get_chat_model
from app.observability import TraceRecorder, get_logger
from app.rag.evidence import EvidenceBundle
from app.rag.prompts import assessment_prompt
from app.retrieval.concepts import CONCEPTS_BY_KEY
from app.retrieval.rerank import RerankedCandidate
from app.schemas.assessment import AssessmentNarrative
from app.schemas.property import PropertyRecord

logger = get_logger(__name__)


@dataclass
class NarrativeResult:
    narrative: AssessmentNarrative
    generated_by: str
    fell_back: bool = False
    fallback_reason: str | None = None


def _target_block(target: PropertyRecord, target_text: str) -> str:
    def show(value: object, suffix: str = "") -> str:
        return f"{value}{suffix}" if value is not None else "not recorded"

    return (
        f"{target.address}\n"
        f"  Type {target.property_type.value}; "
        f"{show(target.bedrooms)} bed, {show(target.bathrooms)} bath, "
        f"{show(target.carspaces)} car; internal {show(target.floor_area_sqm, ' m2')}, "
        f"land {show(target.land_area_sqm, ' m2')}\n"
        f"  Listing text: {target_text[:800]}"
    )


def _price_block(evidence: PriceEvidence, label: str, confidence: str) -> str:
    if not evidence.has_range:
        return "No evidence range could be formed from the selected comparables."
    lines = [
        f"Evidence range : ${evidence.evidence_low:,} - ${evidence.evidence_high:,}",
        f"Median of comparables: ${evidence.median:,}",
        f"Full observed span: ${evidence.observed_low:,} - ${evidence.observed_high:,}",
        f"Method         : {evidence.method}",
        f"Assessment label (already decided): {label}",
        f"Confidence (already decided)      : {confidence}",
    ]
    if evidence.asking_price and evidence.delta_to_high is not None:
        direction = "above" if evidence.delta_to_high > 0 else "below"
        lines.append(
            f"Asking price sits ${abs(evidence.delta_to_high):,} {direction} the top of the "
            f"evidence range."
        )
    for note in evidence.notes:
        lines.append(f"Note: {note}")
    return "\n".join(lines)


def _comparables_block(comparables: list[RerankedCandidate]) -> str:
    if not comparables:
        return "None."
    lines = []
    for item in comparables:
        candidate = item.fused.candidate
        lines.append(
            f"- {candidate.address}: sold ${candidate.sold_price:,} on {candidate.sold_at}, "
            f"{candidate.distance_km:.2f} km away, {candidate.bedrooms}bed/"
            f"{candidate.bathrooms}bath/{candidate.carspaces}car, "
            f"comparability {item.verdict.similarity_score:.2f}"
        )
        if item.verdict.important_differences:
            lines.append(f"    differences: {'; '.join(item.verdict.important_differences[:4])}")
    return "\n".join(lines)


def _offline_narrative(
    target: PropertyRecord,
    evidence: PriceEvidence,
    bundle: EvidenceBundle,
    comparables: list[RerankedCandidate],
    label: str,
) -> AssessmentNarrative:
    """Template narrative, used when no LLM backend is configured."""
    sentences: list[str] = []
    citations = [item.citation_id for item in bundle.items][:12]

    if evidence.has_range and comparables:
        sentences.append(
            f"{evidence.comparables_used} comparable sales were selected, and the middle of "
            f"that group sold between ${evidence.evidence_low:,} and ${evidence.evidence_high:,} "
            f"(median ${evidence.median:,})."
        )
    # Bound to locals so the `has_range` invariant is visible to the type checker:
    # `has_range` is exactly `evidence_low is not None and evidence_high is not None`.
    low, high = evidence.evidence_low, evidence.evidence_high
    if evidence.asking_price and low is not None and high is not None:
        if evidence.asking_price > high:
            gap = evidence.asking_price - high
            sentences.append(
                f"The ${evidence.asking_price:,} asking price sits ${gap:,} above the top of "
                f"that range."
            )
            if label == "fair":
                # Without this the label and the sentence above look contradictory.
                sentences.append(
                    "That gap is inside the tolerance treated as ordinary variation between "
                    "individual sales, which is why the assessment is 'reasonable' rather "
                    "than 'slightly high'."
                )
        elif evidence.asking_price < low:
            sentences.append(
                f"The ${evidence.asking_price:,} asking price sits "
                f"${low - evidence.asking_price:,} below the bottom of that range."
            )
        else:
            sentences.append(
                f"The ${evidence.asking_price:,} asking price falls inside that range."
            )

    shared: dict[str, int] = {}
    for item in comparables:
        for key in item.fused.shared_concepts:
            shared[key] = shared.get(key, 0) + 1
    common = [
        CONCEPTS_BY_KEY[key].label.lower()
        for key, count in sorted(shared.items(), key=lambda pair: -pair[1])[:3]
        if key in CONCEPTS_BY_KEY
    ]
    if common:
        sentences.append(
            f"Features shared with the strongest comparables include {', '.join(common)}."
        )
    sentences.append(
        "This summary was generated by deterministic templates because no language model "
        "backend is configured; it reports the evidence without interpreting it."
    )

    differences: list[str] = []
    for item in comparables[:4]:
        for difference in item.verdict.important_differences[:2]:
            entry = f"{item.fused.candidate.short_address if hasattr(item.fused.candidate,'short_address') else item.fused.candidate.address.split(',')[0]}: {difference}"
            if entry not in differences:
                differences.append(entry)

    unknowns = list(bundle.missing[:6])
    if not unknowns:
        unknowns.append(
            "Internal condition, strata levies, building defects and any recent renovation "
            "not mentioned in the listings were not assessed."
        )
    return AssessmentNarrative(
        reasoning_summary=" ".join(sentences)[:2000],
        important_differences=differences[:8],
        unknowns=unknowns[:8],
        cited_comparable_ids=citations,
    )


async def generate_narrative(
    *,
    target: PropertyRecord,
    target_text: str,
    evidence: PriceEvidence,
    bundle: EvidenceBundle,
    comparables: list[RerankedCandidate],
    label: str,
    confidence: str,
    tracer: TraceRecorder | None = None,
) -> NarrativeResult:
    """Run the RAG narration chain, falling back deterministically on failure."""
    if not llm_is_available():
        narrative = _offline_narrative(target, evidence, bundle, comparables, label)
        if tracer:
            tracer.record(
                "llm",
                "generate_narrative",
                status="skipped",
                backend="offline",
                reason="no LLM credentials configured",
            )
        return NarrativeResult(
            narrative=narrative,
            generated_by="offline:deterministic-template",
            fell_back=True,
            fallback_reason="No LLM backend configured.",
        )

    model = get_chat_model()
    if model is None:  # pragma: no cover - guarded by llm_is_available
        raise LLMUnavailable("Chat model unavailable.")

    chain = assessment_prompt() | model.with_structured_output(AssessmentNarrative)
    inputs = {
        "target_block": _target_block(target, target_text),
        "asking_block": (
            f"${evidence.asking_price:,}" if evidence.asking_price else "No asking price published."
        ),
        "price_block": _price_block(evidence, label, confidence),
        "comparables_block": _comparables_block(comparables),
        "evidence_block": bundle.to_prompt_block(),
        "gaps_block": "\n".join(f"- {gap}" for gap in bundle.missing) or "- None recorded.",
    }

    try:
        with (
            tracer.span("llm", "generate_narrative", backend=llm_backend_name())
            if tracer
            else _NullSpan()
        ):
            result = await chain.ainvoke(inputs)
        narrative = (
            result
            if isinstance(result, AssessmentNarrative)
            else AssessmentNarrative.model_validate(result)
        )
        return NarrativeResult(narrative=narrative, generated_by=llm_backend_name())
    except Exception as exc:
        logger.warning("rag.narrative_failed", error=f"{type(exc).__name__}: {exc}")
        narrative = _offline_narrative(target, evidence, bundle, comparables, label)
        return NarrativeResult(
            narrative=narrative,
            generated_by="offline:deterministic-template",
            fell_back=True,
            fallback_reason=f"LLM narration failed ({type(exc).__name__}); deterministic summary used.",
        )


class _NullSpan:
    def __enter__(self) -> _NullSpan:
        return self

    def __exit__(self, *args: object) -> Literal[False]:
        return False
