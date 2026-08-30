"""RAG evaluation: is the explanation actually grounded in what was retrieved?

Four properties are measured, all mechanically checkable without a second LLM
acting as judge — which matters, because an LLM judge would itself need
validating and would not run in the offline configuration at all.

    retrieval_relevance   share of evidence items that belong to the selected
                          comparables, the target, or its suburb
    groundedness          share of dollar figures in the narrative that trace to
                          the evidence or to the deterministic computation
    citation_correctness  share of cited ids that actually exist
    faithfulness          absence of prohibited claims, and no comparable in the
                          answer that was not in the selected set

`unsupported_claim_rate` is the headline number: the proportion of assessments
containing at least one statement the evidence does not support. For a product
that people make six-figure decisions on, this is the metric that matters most.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agent.graph import run_analysis
from app.guardrails.validators import _MONEY, _normalise_money, check_prohibited_claims
from app.observability import get_logger
from app.schemas.assessment import PriceAssessment

logger = get_logger(__name__)

_CITATION = re.compile(r"\bE\d+\b")


@dataclass
class RagCaseResult:
    query: str
    ok: bool
    retrieval_relevance: float = 0.0
    groundedness: float = 0.0
    citation_correctness: float = 0.0
    faithful: bool = True
    unsupported_claims: list[str] = field(default_factory=list)
    invented_comparables: int = 0
    evidence_items: int = 0
    generated_by: str = ""
    failure: str | None = None


@dataclass
class RagEvalResult:
    cases: list[RagCaseResult] = field(default_factory=list)

    @property
    def unsupported_claim_rate(self) -> float:
        graded = [case for case in self.cases if case.ok]
        if not graded:
            return 0.0
        return sum(1 for case in graded if case.unsupported_claims) / len(graded)

    def average(self, attribute: str) -> float:
        graded = [case for case in self.cases if case.ok]
        if not graded:
            return 0.0
        return sum(getattr(case, attribute) for case in graded) / len(graded)


def _score_case(query: str, final: dict) -> RagCaseResult:
    assessment: PriceAssessment | None = final.get("assessment")
    bundle = final.get("evidence")
    if assessment is None:
        return RagCaseResult(query=query, ok=False, failure="No assessment produced.")

    included = [item for item in (final.get("reranked_comps") or []) if item.included]
    included_ids = {item.fused.candidate.property_id for item in included}
    included_external = {item.fused.candidate.external_id for item in included}
    target = final.get("target_property")
    suburb = target.suburb.lower() if target else ""

    # --- retrieval relevance ------------------------------------------------
    relevant = 0
    total = len(bundle.items) if bundle else 0
    if bundle:
        for item in bundle.items:
            if (
                item.property_id in included_ids
                or item.source_type == "property_summary"
                or (suburb and suburb in item.content.lower())
            ):
                relevant += 1
    retrieval_relevance = relevant / total if total else 0.0

    # --- groundedness --------------------------------------------------------
    supported: set[str] = set()
    for source in (
        bundle.to_prompt_block() if bundle else "",
        " ".join(f"${item.sold_price}" for item in included),
        f"${assessment.asking_price or 0} ${assessment.evidence_range_low or 0} "
        f"${assessment.evidence_range_high or 0} ${assessment.evidence_median or 0}",
    ):
        for match in _MONEY.finditer(source):
            supported |= _normalise_money(match.group(0))
    for value in (
        (assessment.asking_price or 0) - (assessment.evidence_range_high or 0),
        (assessment.asking_price or 0) - (assessment.evidence_range_low or 0),
        (assessment.asking_price or 0) - (assessment.evidence_median or 0),
        (assessment.evidence_range_high or 0) - (assessment.evidence_range_low or 0),
    ):
        supported |= _normalise_money(f"${abs(value)}")

    figures = list(_MONEY.finditer(assessment.reasoning_summary))
    unsupported = [
        match.group(0) for match in figures if not (_normalise_money(match.group(0)) & supported)
    ]
    groundedness = 1.0 - (len(unsupported) / len(figures)) if figures else 1.0

    # --- citation correctness -------------------------------------------------
    allowed = bundle.citation_ids if bundle else set()
    cited = set(assessment.citations) | set(_CITATION.findall(assessment.reasoning_summary))
    citation_correctness = (
        len(cited & allowed) / len(cited) if cited else (1.0 if not allowed else 0.0)
    )

    # --- faithfulness ---------------------------------------------------------
    claims = check_prohibited_claims(assessment.reasoning_summary)
    for difference in assessment.important_differences:
        claims.extend(check_prohibited_claims(difference))
    invented = sum(
        1 for item in assessment.supporting_comparables if item.external_id not in included_external
    )

    problems = [f"prohibited claim ({name})" for name, _ in claims]
    problems.extend(f"unsupported figure {figure}" for figure in unsupported)
    problems.extend(f"citation {citation} not retrieved" for citation in sorted(cited - allowed))
    if invented:
        problems.append(f"{invented} comparable(s) not in the selected set")

    return RagCaseResult(
        query=query,
        ok=True,
        retrieval_relevance=round(retrieval_relevance, 3),
        groundedness=round(groundedness, 3),
        citation_correctness=round(citation_correctness, 3),
        faithful=not claims and invented == 0,
        unsupported_claims=problems,
        invented_comparables=invented,
        evidence_items=total,
        generated_by=assessment.generated_by,
    )


async def evaluate_rag(queries: list[str]) -> RagEvalResult:
    result = RagEvalResult()
    for index, query in enumerate(queries):
        try:
            final, _tracer, _ms = await run_analysis(analysis_id=f"eval-rag-{index}", query=query)
            result.cases.append(_score_case(query, final))
        except Exception as exc:
            logger.warning("eval.rag_case_failed", query=query, error=str(exc))
            result.cases.append(
                RagCaseResult(query=query, ok=False, failure=f"{type(exc).__name__}: {exc}")
            )
    return result


def render_rag(result: RagEvalResult) -> str:
    lines = ["", "RAG EVALUATION", "=" * 78, ""]
    lines.append(f"{'metric':<26}{'score':>8}   interpretation")
    lines.append("-" * 78)
    rows = [
        (
            "retrieval_relevance",
            result.average("retrieval_relevance"),
            "evidence tied to the target, its suburb, or a selected comparable",
        ),
        (
            "groundedness",
            result.average("groundedness"),
            "dollar figures traceable to evidence or computation",
        ),
        (
            "citation_correctness",
            result.average("citation_correctness"),
            "cited evidence ids that actually exist",
        ),
    ]
    for name, value, note in rows:
        lines.append(f"{name:<26}{value:>8.3f}   {note}")
    faithful = sum(1 for case in result.cases if case.ok and case.faithful)
    graded = sum(1 for case in result.cases if case.ok)
    lines.append(
        f"{'faithfulness':<26}{(faithful / graded if graded else 0):>8.3f}   no prohibited claims, no invented comparables"
    )
    lines.append(
        f"{'unsupported_claim_rate':<26}{result.unsupported_claim_rate:>8.3f}   share of answers with ANY unsupported statement (lower is better)"
    )
    lines.append("")
    lines.append("PER CASE")
    for case in result.cases:
        if not case.ok:
            lines.append(f"  FAIL  {case.query[:56]:<58} {case.failure}")
            continue
        status = "OK  " if not case.unsupported_claims else "WARN"
        lines.append(
            f"  {status}  {case.query[:56]:<58} evidence={case.evidence_items:<3} "
            f"by={case.generated_by}"
        )
        for problem in case.unsupported_claims[:4]:
            lines.append(f"          - {problem}")
    return "\n".join(lines)
