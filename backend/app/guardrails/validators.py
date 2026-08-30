"""Final validation before anything reaches the user.

This runs on every assessment, whether an LLM was involved or not. Rule-based
templates can also drift into overclaiming, and the user cannot tell which
component produced a sentence — so both are checked identically.

FIVE CHECKS
-----------
1. **Prohibited claims.** No guaranteed value, no appreciation or yield forecast,
   no "professional valuation", no financial or legal advice, no point valuation
   ("worth exactly $X"). These are regulatory and ethical lines, not style
   preferences, so a match removes the sentence rather than warning about it.

2. **Citation integrity.** Every cited id must exist in the retrieved evidence.
   A citation to `E9` when only E1-E7 were retrieved is a fabricated source, and
   fabricated sources are worse than no sources.

3. **No invented comparables.** `supporting_comparables` may only contain
   properties that survived retrieval and grading, with the sale prices recorded
   in the database. Prices are re-read from the database rather than trusted from
   the model, so a hallucinated figure cannot survive.

4. **Unsupported figures.** Dollar amounts in the narrative must appear in the
   computed evidence or the retrieved text. A number the user might negotiate
   against cannot come from nowhere.

5. **Confidence coherence.** Confidence may never exceed what the evidence
   quality supports, and `insufficient_evidence` may never be paired with high
   confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.observability import get_logger
from app.schemas.assessment import (
    AssessmentLabel,
    ComparableCitation,
    ConfidenceLevel,
    EvidenceQuality,
    PriceAssessment,
)

logger = get_logger(__name__)

DISCLAIMER = (
    "This is an evidence-based comparable-sales analysis, not a professional property "
    "valuation or financial advice."
)

#: Each entry is a claim this product must never make.
_PROHIBITED: list[tuple[str, re.Pattern[str]]] = [
    (
        "guaranteed value",
        re.compile(
            r"\b(guarantee[ds]?|certain(ly)?|definitely)\b[^.]{0,40}\b(worth|value|price)\b", re.I
        ),
    ),
    (
        "point valuation",
        re.compile(r"\b(is|are)\s+(actually\s+)?worth\s+(exactly\s+)?\$", re.I),
    ),
    (
        "market value claim",
        re.compile(
            r"\b(the\s+)?(true|actual|real|fair)\s+market\s+value\s+(is|of this property is)\b",
            re.I,
        ),
    ),
    (
        "appreciation forecast",
        re.compile(
            r"\b(will|should|is going to|expected to)\s+(appreciate|increase in value|rise in value|grow in value|go up in value)\b",
            re.I,
        ),
    ),
    (
        "future price prediction",
        re.compile(
            r"\bin\s+(\d+|a few|several)\s+years?\b[^.]{0,60}\b(worth|value|sell for)\b", re.I
        ),
    ),
    (
        "investment return claim",
        re.compile(
            r"\b(return on investment|roi|capital growth of|rental yield of|guaranteed return)\b",
            re.I,
        ),
    ),
    (
        "professional valuation claim",
        re.compile(
            r"\b(this is|constitutes|serves as)\s+(a\s+)?(professional|formal|certified|sworn)\s+valuation\b",
            re.I,
        ),
    ),
    (
        "financial advice",
        re.compile(
            r"\b(you should (buy|offer|bid|purchase|invest)|we recommend (buying|offering|paying)|my advice is)\b",
            re.I,
        ),
    ),
    (
        "legal advice",
        re.compile(
            r"\b(legally|from a legal standpoint)[^.]{0,40}\b(you (must|should)|advise)\b", re.I
        ),
    ),
]

_MONEY = re.compile(r"\$\s?(\d[\d,]*)(?:\.\d+)?\s?(k|m|million)?", re.IGNORECASE)
_CITATION = re.compile(r"\bE\d+\b")


@dataclass
class GuardrailReport:
    notes: list[str] = field(default_factory=list)
    blocked_claims: list[str] = field(default_factory=list)
    removed_citations: list[str] = field(default_factory=list)
    unsupported_figures: list[str] = field(default_factory=list)
    confidence_downgraded: bool = False

    @property
    def passed_clean(self) -> bool:
        return not (self.blocked_claims or self.removed_citations or self.unsupported_figures)


def check_prohibited_claims(text: str) -> list[tuple[str, str]]:
    """Return `(rule_name, offending_sentence)` for every prohibited claim found."""
    findings: list[tuple[str, str]] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        for name, pattern in _PROHIBITED:
            if pattern.search(sentence):
                findings.append((name, sentence.strip()))
                break
    return findings


def _strip_prohibited(text: str, report: GuardrailReport) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text or "")
    kept: list[str] = []
    for sentence in sentences:
        offending = next((name for name, pattern in _PROHIBITED if pattern.search(sentence)), None)
        if offending:
            report.blocked_claims.append(f"{offending}: {sentence.strip()[:160]}")
            continue
        kept.append(sentence)
    return " ".join(part for part in kept if part).strip()


def _normalise_money(token: str) -> set[str]:
    """Variants of a dollar figure so $915,000 / $915000 / $915k all compare equal."""
    digits = token.replace(",", "").replace("$", "").strip().lower()
    variants = {digits}
    try:
        if digits.endswith("k"):
            variants.add(str(int(float(digits[:-1]) * 1_000)))
        elif digits.endswith("m") or digits.endswith("million"):
            variants.add(str(int(float(re.sub(r"(m|million)$", "", digits)) * 1_000_000)))
        else:
            value = int(float(digits))
            variants.add(str(value))
            if value >= 1000 and value % 1000 == 0:
                variants.add(f"{value // 1000}k")
    except ValueError:
        pass
    return variants


def _supported_figures(*sources: str) -> set[str]:
    supported: set[str] = set()
    for source in sources:
        for match in _MONEY.finditer(source or ""):
            supported |= _normalise_money(match.group(0))
    return supported


def apply_guardrails(
    assessment: PriceAssessment,
    *,
    allowed_citation_ids: set[str],
    allowed_comparables: dict[str, ComparableCitation],
    evidence_text: str,
    evidence_quality: EvidenceQuality,
    derived_figures: set[int] | None = None,
) -> tuple[PriceAssessment, GuardrailReport]:
    """Validate and, where necessary, repair the assessment. Never silently passes.

    `derived_figures` are values the deterministic pricing layer computed itself
    (for example the gap between the asking price and the top of the range). They
    are legitimately supported even though they appear in no source document, and
    without this the check would flag our own arithmetic as a hallucination.
    """
    report = GuardrailReport()

    # --- 1. Prohibited claims -------------------------------------------------
    summary = _strip_prohibited(assessment.reasoning_summary, report)
    differences = [
        item for item in assessment.important_differences if not check_prohibited_claims(item)
    ]
    if len(differences) != len(assessment.important_differences):
        report.notes.append("Removed a stated difference that made a prohibited claim.")

    # --- 2. Citation integrity ------------------------------------------------
    citations: list[str] = []
    for citation in assessment.citations:
        if citation in allowed_citation_ids:
            citations.append(citation)
        else:
            report.removed_citations.append(citation)
    for cited in _CITATION.findall(summary):
        if cited not in allowed_citation_ids:
            summary = summary.replace(f"[{cited}]", "").replace(cited, "")
            report.removed_citations.append(cited)
    if report.removed_citations:
        report.notes.append(
            f"Removed {len(report.removed_citations)} citation(s) that referred to evidence "
            f"which was never retrieved: {', '.join(sorted(set(report.removed_citations)))}."
        )

    # --- 3. No invented comparables -------------------------------------------
    verified: list[ComparableCitation] = []
    for comparable in assessment.supporting_comparables:
        truth = allowed_comparables.get(comparable.external_id)
        if truth is None:
            report.blocked_claims.append(
                f"invented comparable: {comparable.address} (${comparable.sold_price:,})"
            )
            continue
        # Re-read from the database record rather than trusting the model.
        verified.append(truth)
    if len(verified) != len(assessment.supporting_comparables):
        report.notes.append(
            "One or more supporting comparables were not part of the selected set and were removed."
        )

    # --- 4. Unsupported dollar figures ----------------------------------------
    supported = _supported_figures(
        evidence_text,
        " ".join(f"${item.sold_price}" for item in verified),
        f"${assessment.asking_price or 0} ${assessment.evidence_range_low or 0} "
        f"${assessment.evidence_range_high or 0} ${assessment.evidence_median or 0}",
        " ".join(f"${abs(value)}" for value in (derived_figures or set())),
    )
    for match in _MONEY.finditer(summary):
        if not (_normalise_money(match.group(0)) & supported):
            report.unsupported_figures.append(match.group(0))
    if report.unsupported_figures:
        report.notes.append(
            f"Narrative referenced dollar figures not present in the evidence: "
            f"{', '.join(sorted(set(report.unsupported_figures)))}. Treat the summary with caution."
        )

    # --- 5. Confidence coherence ----------------------------------------------
    confidence = assessment.confidence
    ceiling = {
        EvidenceQuality.STRONG: ConfidenceLevel.HIGH,
        EvidenceQuality.ADEQUATE: ConfidenceLevel.MEDIUM,
        EvidenceQuality.WEAK: ConfidenceLevel.LOW,
        EvidenceQuality.INSUFFICIENT: ConfidenceLevel.LOW,
    }[evidence_quality]
    order = [ConfidenceLevel.LOW, ConfidenceLevel.MEDIUM, ConfidenceLevel.HIGH]
    if order.index(confidence) > order.index(ceiling):
        report.confidence_downgraded = True
        report.notes.append(
            f"Confidence reduced from {confidence.value} to {ceiling.value} to match "
            f"{evidence_quality.value} evidence quality."
        )
        confidence = ceiling
    if (
        assessment.assessment is AssessmentLabel.INSUFFICIENT_EVIDENCE
        and confidence is not ConfidenceLevel.LOW
    ):
        report.confidence_downgraded = True
        confidence = ConfidenceLevel.LOW
        report.notes.append("Confidence forced to low: the assessment is insufficient_evidence.")

    if not verified and assessment.assessment is not AssessmentLabel.INSUFFICIENT_EVIDENCE:
        report.notes.append(
            "No verified comparables remained, so the assessment was downgraded to "
            "insufficient_evidence."
        )
        assessment = assessment.model_copy(
            update={"assessment": AssessmentLabel.INSUFFICIENT_EVIDENCE}
        )

    unknowns = list(assessment.unknowns)
    if report.unsupported_figures:
        unknowns.append("Some figures in the summary could not be traced to retrieved evidence.")

    cleaned = assessment.model_copy(
        update={
            "reasoning_summary": summary,
            "important_differences": differences,
            "citations": citations,
            "supporting_comparables": verified,
            "confidence": confidence,
            "unknowns": unknowns,
            "guardrail_notes": report.notes,
        }
    )
    logger.info(
        "guardrails.applied",
        blocked=len(report.blocked_claims),
        removed_citations=len(report.removed_citations),
        unsupported_figures=len(report.unsupported_figures),
        confidence_downgraded=report.confidence_downgraded,
    )
    return cleaned, report
