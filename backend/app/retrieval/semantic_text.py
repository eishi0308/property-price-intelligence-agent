"""Build the text that actually gets embedded.

What we embed matters more than which model embeds it. Two rules:

1. **Qualitative only.** Bedrooms, price, distance and dates are handled
   deterministically by SQL — putting them in the embedding just adds noise and
   invites the vector index to rank on things it represents badly.

2. **Provenance-preserving.** The document is assembled from the listing's own
   words. Nothing is paraphrased or invented on the way in, so a citation back to
   the source listing remains truthful.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.retrieval.concepts import detect_concepts
from app.schemas.property import ListingRecord, PropertyRecord

_WHITESPACE = re.compile(r"\s+")
_BOILERPLATE = re.compile(
    r"(disclaimer|all information contained herein|whilst every care|"
    r"prospective purchasers should|inspect by appointment|contact agent|"
    r"all measurements are approximate)[^.]*\.",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SemanticDocument:
    text: str
    concepts: dict[str, str]
    source: str
    has_real_description: bool

    @property
    def is_degraded(self) -> bool:
        """True when built from attributes alone — semantic recall will be poor."""
        return not self.has_real_description


def clean_listing_text(text: str | None) -> str:
    if not text:
        return ""
    stripped = _BOILERPLATE.sub(" ", text)
    return _WHITESPACE.sub(" ", stripped).strip()


def _structural_sentence(record: PropertyRecord) -> str:
    """Minimal fallback prose when no description exists.

    Deliberately thin: if this is all we have, the honest outcome is weak semantic
    retrieval, and the feasibility report and API both say so.
    """
    parts = [f"{record.property_type.value.replace('_', ' ')}"]
    if record.bedrooms is not None:
        parts.append(f"{record.bedrooms} bedroom")
    if record.bathrooms is not None:
        parts.append(f"{record.bathrooms} bathroom")
    if record.carspaces:
        parts.append(f"{record.carspaces} car space")
    elif record.carspaces == 0:
        parts.append("no car space")
    if record.floor_area_sqm:
        parts.append(f"{record.floor_area_sqm:g} square metre internal")
    return f"A {' '.join(parts)} property in {record.suburb}."


def build_semantic_document(
    record: PropertyRecord, listing: ListingRecord | None
) -> SemanticDocument:
    """Assemble the embedding input for one property."""
    description = clean_listing_text(listing.description if listing else None)
    headline = clean_listing_text(listing.headline if listing else None)

    has_description = len(description) >= 80
    if has_description:
        body = f"{headline}. {description}" if headline else description
        source = "listing_description"
    else:
        body = " ".join(part for part in (headline, description) if part) or _structural_sentence(
            record
        )
        source = "structured_attributes"

    concepts = detect_concepts(body)
    return SemanticDocument(
        text=body.strip(),
        concepts=concepts,
        source=source,
        has_real_description=has_description,
    )


def build_query_document(record: PropertyRecord, listing: ListingRecord | None) -> SemanticDocument:
    """The target's semantic document, used as the vector-search query.

    Identical construction to the corpus side — asymmetry between query and
    document construction is a classic silent retrieval-quality bug.
    """
    return build_semantic_document(record, listing)
