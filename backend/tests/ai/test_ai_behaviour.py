"""AI-behaviour tests.

These assert the properties that make the output *safe to act on*, independent of
which backend produced it. They run in both configurations:

* with an LLM configured, they exercise the real structured-output path;
* with none, they exercise the deterministic fallbacks.

Either way the invariants are identical, which is the point — a user must get the
same guarantees regardless of how the answer was generated.
"""

from __future__ import annotations

import uuid

import pytest

from app.agent.graph import run_analysis
from app.assessment.pricing import ComparableSale, compute_price_evidence
from app.guardrails.validators import check_prohibited_claims
from app.llm import llm_is_available
from app.rag.chains import generate_narrative
from app.retrieval.rerank import DeterministicRelevanceGrader
from app.schemas.assessment import AssessmentNarrative, PriceAssessment, RerankVerdict
from tests.conftest import TARGET_QUERY

pytestmark = [pytest.mark.ai, pytest.mark.integration]


@pytest.fixture(scope="module")
async def analysis(database_ready):
    final, tracer, _ms = await run_analysis(analysis_id=str(uuid.uuid4()), query=TARGET_QUERY)
    return final, tracer


# --- structured output ----------------------------------------------------
def test_rerank_verdict_schema_rejects_out_of_range_scores():
    with pytest.raises(Exception):
        RerankVerdict(comparable=True, similarity_score=1.5)
    with pytest.raises(Exception):
        RerankVerdict(comparable=True, similarity_score=-0.1)


def test_rerank_verdict_schema_forbids_extra_fields():
    """`extra="forbid"` is what stops a model smuggling in an invented price."""
    with pytest.raises(Exception):
        RerankVerdict(comparable=True, similarity_score=0.8, estimated_value=900_000)


def test_narrative_schema_forbids_numeric_fields_entirely():
    """The narrator has no field in which to put a price — by construction."""
    assert set(AssessmentNarrative.model_fields) == {
        "reasoning_summary",
        "important_differences",
        "unknowns",
        "cited_comparable_ids",
    }
    with pytest.raises(Exception):
        AssessmentNarrative(reasoning_summary="x", evidence_range_low=1)


async def test_every_grader_verdict_validates_against_the_schema(analysis):
    final, _tracer = analysis
    for item in final["reranked_comps"]:
        assert isinstance(item.verdict, RerankVerdict)
        assert 0.0 <= item.verdict.similarity_score <= 1.0
        assert isinstance(item.verdict.important_matches, list)


# --- no hallucinated comparables ------------------------------------------
async def test_no_comparable_in_the_answer_was_invented(analysis):
    final, _tracer = analysis
    assessment: PriceAssessment = final["assessment"]
    real = {
        item.fused.candidate.external_id: item.fused.candidate
        for item in final["reranked_comps"]
        if item.included
    }
    for cited in assessment.supporting_comparables:
        assert cited.external_id in real, f"{cited.address} is not in the selected set"
        # Prices are re-read from the database, so they cannot drift from the truth.
        assert cited.sold_price == real[cited.external_id].sold_price
        assert cited.address == real[cited.external_id].address


async def test_the_target_property_is_never_its_own_comparable(analysis):
    final, _tracer = analysis
    target_id = final["target_property"].external_id
    assert all(item.fused.candidate.external_id != target_id for item in final["reranked_comps"])


# --- grounded citations ----------------------------------------------------
async def test_every_citation_refers_to_retrieved_evidence(analysis):
    final, _tracer = analysis
    assessment: PriceAssessment = final["assessment"]
    allowed = final["evidence"].citation_ids
    assert set(assessment.citations) <= allowed


async def test_evidence_spans_multiple_retrieval_domains(analysis):
    final, _tracer = analysis
    coverage = final["evidence"].coverage()
    assert coverage.get("listing_description", 0) >= 3, "comparable evidence is mandatory"
    assert len(coverage) >= 2, "RAG must draw on more than one evidence domain"


# --- guardrails on real output ---------------------------------------------
async def test_generated_answer_makes_no_prohibited_claim(analysis):
    final, _tracer = analysis
    assessment: PriceAssessment = final["assessment"]
    assert check_prohibited_claims(assessment.reasoning_summary) == []
    for difference in assessment.important_differences:
        assert check_prohibited_claims(difference) == []


async def test_answer_declares_how_it_was_generated(analysis):
    final, _tracer = analysis
    assessment: PriceAssessment = final["assessment"]
    assert assessment.generated_by
    if not llm_is_available():
        assert assessment.generated_by.startswith("offline:")
    assert assessment.is_demo_data is True, "demo data must always be labelled as such"


async def test_unknowns_are_always_surfaced(analysis):
    final, _tracer = analysis
    assert final["assessment"].unknowns, "an honest answer must state what it could not establish"


# --- insufficient evidence handling ----------------------------------------
async def test_no_comparables_yields_insufficient_evidence_not_a_guess():
    evidence = compute_price_evidence([], 950_000)
    assert not evidence.has_range
    assert evidence.evidence_low is None


async def test_narrative_falls_back_cleanly_without_an_llm(analysis):
    final, _tracer = analysis
    included = [item for item in final["reranked_comps"] if item.included]
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
        final["asking_price"],
    )
    result = await generate_narrative(
        target=final["target_property"],
        target_text=final["target_text"],
        evidence=evidence,
        bundle=final["evidence"],
        comparables=included,
        label="fair",
        confidence="medium",
    )
    assert isinstance(result.narrative, AssessmentNarrative)
    assert result.narrative.reasoning_summary
    if not llm_is_available():
        assert result.fell_back
        assert "no language model" in result.narrative.reasoning_summary.lower()


async def test_grader_never_invents_figures_in_its_verdicts(analysis):
    """The reranker's own anti-invention filter must leave the verdicts clean."""
    final, _tracer = analysis
    for item in final["reranked_comps"][:10]:
        for phrase in item.verdict.important_matches + item.verdict.important_differences:
            assert "$" not in phrase, "graders must not speculate in dollar terms"


async def test_reranker_selects_a_bounded_number_of_comparables(analysis):
    final, _tracer = analysis
    included = [item for item in final["reranked_comps"] if item.included]
    assert 0 < len(included) <= 10
    scores = [item.verdict.similarity_score for item in final["reranked_comps"]]
    assert scores == sorted(scores, reverse=True), "graded output must be ordered by comparability"


async def test_offline_grader_is_used_and_labelled_when_no_llm_is_configured(analysis):
    final, _tracer = analysis
    if llm_is_available():
        pytest.skip("An LLM is configured; the offline path is not exercised.")
    assert final["degraded_grader"] is True
    assert all(
        item.graded_by == DeterministicRelevanceGrader.name for item in final["reranked_comps"]
    )
    assert any(
        "deterministic rules" in reason for reason in final["evidence_quality_reasons"]
    ), "degraded grading must be disclosed to the user"


async def test_confidence_is_never_high_when_both_backends_are_degraded(analysis):
    final, _tracer = analysis
    if llm_is_available():
        pytest.skip("An LLM is configured.")
    assert final["assessment"].confidence.value in {"low", "medium"}
