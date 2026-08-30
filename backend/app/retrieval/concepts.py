"""Property concept lexicon.

One shared vocabulary, used for three different jobs:

* building the keyword arm's tsquery (explicit terms only),
* the offline deterministic encoder's concept dimensions,
* explaining *why* two properties matched, in the UI.

Keeping it in one place means the keyword arm and the semantic arm are provably
looking for the same underlying concepts through different mechanisms — which is
the entire premise of hybrid retrieval.

This is a hand-authored domain lexicon, not a learned artefact. It is never
fitted, trained or optimised against data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Concept:
    key: str
    label: str
    #: Terms a buyer would actually type; drive PostgreSQL full-text search.
    keyword_terms: tuple[str, ...]
    #: Paraphrases carrying the same meaning with no shared keyword.
    paraphrases: tuple[str, ...] = field(default_factory=tuple)
    #: Directional effect on price, used only to *explain*, never to compute one.
    direction: str = "positive"  # positive | negative | neutral


CONCEPTS: tuple[Concept, ...] = (
    Concept(
        "north_facing",
        "Northerly aspect",
        ("north facing", "north-facing", "northerly", "northern aspect", "due north"),
        (
            "morning sun",
            "sun drenched",
            "sun filled",
            "afternoon sun",
            "captures the sun",
            "bathed in sun",
            "sunny aspect",
            "sun tracks",
        ),
    ),
    Concept(
        "renovated",
        "Renovated / updated",
        ("renovated", "renovation", "refurbished", "updated", "remodelled", "brand new kitchen"),
        (
            "reimagined",
            "contemporary update",
            "freshly presented",
            "stone benchtops",
            "new joinery",
            "hard work already done",
            "designer finishes",
            "immaculately presented",
        ),
    ),
    Concept(
        "parking",
        "Secure parking",
        (
            "parking",
            "car space",
            "carspace",
            "garage",
            "lock up garage",
            "carport",
            "car accommodation",
        ),
        (
            "off street",
            "basement",
            "remote controlled gates",
            "lock-up garaging",
            "space for the family car",
            "undercover space",
        ),
    ),
    Concept(
        "balcony",
        "Balcony / outdoor entertaining",
        ("balcony", "terrace", "deck", "verandah", "patio"),
        (
            "outdoor entertaining",
            "alfresco",
            "outdoor room",
            "entertaining space",
            "flows to the outdoors",
            "outdoor living",
        ),
    ),
    Concept(
        "quiet_position",
        "Quiet position",
        ("quiet", "peaceful", "tranquil", "rear aspect", "cul de sac", "tree lined"),
        (
            "away from through traffic",
            "birdsong",
            "whisper",
            "insulated from the street",
            "secluded",
            "tucked away",
            "serene setting",
        ),
    ),
    Concept(
        "natural_light",
        "Natural light",
        ("natural light", "light filled", "light-filled", "bright", "sunlit", "skylight"),
        (
            "wall to wall glazing",
            "airy",
            "double height glass",
            "oversized windows",
            "luminous",
            "dawn until dusk",
            "lifts every room",
        ),
    ),
    Concept(
        "top_floor",
        "Top floor",
        ("top floor", "top-floor", "penthouse", "upper level"),
        ("nobody above", "uppermost level", "elevated crown position", "highest tier"),
    ),
    Concept(
        "district_views",
        "Views / outlook",
        ("views", "view", "outlook", "district views", "water views", "skyline"),
        (
            "across the treetops",
            "uninterrupted vistas",
            "panorama",
            "over the rooftops",
            "green outlook",
            "to the horizon",
        ),
    ),
    Concept(
        "courtyard",
        "Courtyard / garden",
        ("courtyard", "garden", "backyard", "lawn", "yard"),
        (
            "walled retreat",
            "outdoor sanctuary",
            "wrapped in greenery",
            "ground level outdoor space",
            "morning coffee outside",
        ),
    ),
    Concept(
        "modern_building",
        "Modern building",
        ("modern", "security building", "boutique", "lift", "elevator", "new development"),
        ("recently completed", "handful of residences", "contemporary complex", "intercom access"),
    ),
    Concept(
        "main_road",
        "Main-road position",
        ("main road", "busy road", "highway", "arterial", "parramatta road", "main street"),
        ("well connected route", "busier thoroughfare", "traffic noise"),
        direction="negative",
    ),
    Concept(
        "original_condition",
        "Original / unrenovated",
        ("original condition", "unrenovated", "renovator", "needs work", "original"),
        (
            "blank canvas",
            "scope to add value",
            "awaiting modernisation",
            "same family for decades",
            "update to your own taste",
            "opportunity to improve",
        ),
        direction="negative",
    ),
    Concept(
        "ground_floor",
        "Ground floor",
        ("ground floor", "ground-floor", "street level"),
        ("no stairs to climb", "level access"),
        direction="negative",
    ),
    Concept(
        "small_layout",
        "Compact layout",
        ("compact", "cosy", "studio"),
        ("efficient layout", "makes the most of every metre"),
        direction="negative",
    ),
    Concept(
        "air_conditioning",
        "Climate control",
        ("air conditioning", "ducted", "reverse cycle", "heating", "split system"),
        ("climate controlled", "comfortable year round"),
        direction="neutral",
    ),
    Concept(
        "storage",
        "Storage",
        ("storage", "storage cage", "built in robes", "built-in wardrobes", "walk in robe"),
        ("ample cupboard space", "generous robes"),
        direction="neutral",
    ),
)

CONCEPTS_BY_KEY: dict[str, Concept] = {concept.key: concept for concept in CONCEPTS}

_WORD = re.compile(r"[a-z][a-z'\-]+")


def tokenize(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def detect_concepts(text: str) -> dict[str, str]:
    """Which concepts a piece of text mentions, and via which register.

    Returns ``{concept_key: "keyword" | "paraphrase"}``. Keyword evidence wins
    when a text contains both, because it is the stronger signal.
    """
    haystack = " ".join(tokenize(text))
    found: dict[str, str] = {}
    for concept in CONCEPTS:
        if any(term in haystack for term in concept.keyword_terms):
            found[concept.key] = "keyword"
        elif any(phrase in haystack for phrase in concept.paraphrases):
            found[concept.key] = "paraphrase"
    return found


def concept_labels(keys: list[str] | dict[str, str]) -> list[str]:
    iterable = keys.keys() if isinstance(keys, dict) else keys
    return [CONCEPTS_BY_KEY[key].label for key in iterable if key in CONCEPTS_BY_KEY]


def keyword_terms_for(keys: list[str] | dict[str, str]) -> list[str]:
    """Explicit search terms for the concepts present — feeds the tsquery."""
    iterable = keys.keys() if isinstance(keys, dict) else keys
    terms: list[str] = []
    for key in iterable:
        concept = CONCEPTS_BY_KEY.get(key)
        if concept:
            terms.extend(concept.keyword_terms[:3])
    return terms
