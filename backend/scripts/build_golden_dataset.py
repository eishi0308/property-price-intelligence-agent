#!/usr/bin/env python3
"""Build the retrieval golden dataset.

AVOIDING A CIRCULAR EVALUATION
------------------------------
Ground truth here is derived from the fixture *generative process* — the concept
tags (`_concepts`) assigned when each synthetic listing was created, plus the
recorded structural attributes. The retrieval pipeline never sees those tags: it
only ever reads the rendered listing prose, in which each concept appears in one
of two unpredictable registers. So the labels are independent of the system being
measured, which is the whole point of a golden set.

WHAT COUNTS AS A "KNOWN GOOD" COMPARABLE
----------------------------------------
A sold property qualifies for a target when a competent human valuer would accept
it without argument:

    same property type
    within 2.5 km
    sold within the last 12 months
    bedroom count within ±1
    internal area within ±25%
    shares at least half the target's qualitative concepts

The last clause is what makes the set discriminating. Without it, every structural
near-match would qualify and a distance-sorted SQL query would score perfectly,
telling us nothing about whether semantic retrieval earns its place.

HONEST LIMITATION
-----------------
These labels are valid for the demo corpus only. Against live provider data the
same script cannot run, because no ground-truth concept tags exist — real
evaluation there needs human-labelled comparables. That is stated in the report.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.demo import load_fixture

OUTPUT = Path(__file__).resolve().parents[1] / "evals" / "golden" / "comparables.json"
TODAY = date(2026, 8, 30)

MAX_DISTANCE_KM = 2.5
MAX_MONTHS = 12
MAX_BEDROOM_GAP = 1
MAX_AREA_RATIO = 0.25
MIN_CONCEPT_OVERLAP = 0.5

#: Held-out sold properties used as extra evaluation targets. Three on-market
#: targets is far too small a sample to draw conclusions from — with ~30 cases the
#: comparison between retrieval arms becomes worth acting on. A sold property makes
#: a legitimate pseudo-target: it has the same attributes and listing prose as a
#: live listing, and it is excluded from its own candidate set.
HELDOUT_TARGET_COUNT = 30
MIN_RELEVANT_TO_KEEP = 3


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r1, o1, r2, o2 = map(radians, (lat1, lon1, lat2, lon2))
    h = sin((r2 - r1) / 2) ** 2 + cos(r1) * cos(r2) * sin((o2 - o1) / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(h))


def build() -> dict:
    data = load_fixture()
    properties = {item["external_id"]: item for item in data["properties"]}
    listings_by_property: dict[str, list[dict]] = {}
    for listing in data["listings"]:
        listings_by_property.setdefault(listing["property_external_id"], []).append(listing)

    def _case_for(target: dict, query: str, kind: str) -> dict | None:
        target_concepts = set(target["_concepts"])
        relevant: list[dict] = []
        near_miss: list[dict] = []

        for external_id, candidate in properties.items():
            # A property is never a comparable for itself, and the on-market targets
            # have no sale price so they can never be comparables.
            if external_id == target["external_id"] or external_id.startswith("DEMO-T"):
                continue
            sold = [
                item
                for item in listings_by_property.get(external_id, [])
                if item["status"] == "sold"
            ]
            if not sold:
                continue
            listing = sold[0]

            if candidate["property_type"] != target["property_type"]:
                continue
            distance = _distance_km(
                target["latitude"],
                target["longitude"],
                candidate["latitude"],
                candidate["longitude"],
            )
            months = (TODAY - date.fromisoformat(listing["sold_at"])).days / 30.44
            bed_gap = abs((candidate["bedrooms"] or 0) - (target["bedrooms"] or 0))
            area_ratio = (
                abs(candidate["floor_area_sqm"] - target["floor_area_sqm"])
                / target["floor_area_sqm"]
                if candidate.get("floor_area_sqm") and target.get("floor_area_sqm")
                else 1.0
            )
            candidate_concepts = set(candidate["_concepts"])
            overlap = (
                len(target_concepts & candidate_concepts) / len(target_concepts)
                if target_concepts
                else 0.0
            )

            structural_ok = (
                distance <= MAX_DISTANCE_KM
                and months <= MAX_MONTHS
                and bed_gap <= MAX_BEDROOM_GAP
                and area_ratio <= MAX_AREA_RATIO
            )
            entry = {
                "external_id": external_id,
                "address": candidate["address"],
                "sold_price": listing["sold_price"],
                "sold_at": listing["sold_at"],
                "distance_km": round(distance, 3),
                "months_ago": round(months, 1),
                "bedroom_gap": bed_gap,
                "area_ratio": round(area_ratio, 3),
                "concept_overlap": round(overlap, 3),
                "shared_concepts": sorted(target_concepts & candidate_concepts),
            }
            if structural_ok and overlap >= MIN_CONCEPT_OVERLAP:
                relevant.append(entry)
            elif structural_ok:
                # Structurally fine but qualitatively different: the discriminating
                # cases. A system that ranks these above the truly relevant ones is
                # matching on attributes and calling it semantics.
                near_miss.append(entry)

        relevant.sort(key=lambda item: (-item["concept_overlap"], item["distance_km"]))
        near_miss.sort(key=lambda item: item["distance_km"])
        return {
            "kind": kind,
            "target_external_id": target["external_id"],
            "target_address": target["address"],
            "target_query": query,
            "target_type": target["property_type"],
            "target_bedrooms": target["bedrooms"],
            "target_concepts": sorted(target_concepts),
            "relevant_ids": [item["external_id"] for item in relevant],
            "relevant": relevant,
            "near_miss_ids": [item["external_id"] for item in near_miss],
            "near_miss_count": len(near_miss),
        }

    cases: list[dict] = []

    # The three real on-market targets: the cases the product actually serves.
    for target_spec in data["targets"]:
        case = _case_for(
            properties[target_spec["external_id"]], target_spec["aliases"][0], "on_market_target"
        )
        if case:
            cases.append(case)

    # Held-out sold properties, for statistical weight. Deterministic selection so
    # the dataset is reproducible.
    sold_ids = sorted(
        external_id
        for external_id in properties
        if external_id.startswith("DEMO-P")
        and any(item["status"] == "sold" for item in listings_by_property.get(external_id, []))
    )
    stride = max(1, len(sold_ids) // HELDOUT_TARGET_COUNT)
    for external_id in sold_ids[::stride][:HELDOUT_TARGET_COUNT]:
        case = _case_for(
            properties[external_id], properties[external_id]["address"], "heldout_sold"
        )
        if case and len(case["relevant_ids"]) >= MIN_RELEVANT_TO_KEEP:
            cases.append(case)

    return {
        "_meta": {
            "generator": "scripts/build_golden_dataset.py",
            "corpus": "demo fixtures only — labels are not valid for live provider data",
            "case_kinds": {
                "on_market_target": "the three live listings the product is built to analyse",
                "heldout_sold": (
                    "sold properties used as pseudo-targets purely to give the retrieval "
                    "comparison enough cases to be meaningful; excluded from their own "
                    "candidate sets"
                ),
            },
            "criteria": {
                "max_distance_km": MAX_DISTANCE_KM,
                "max_months": MAX_MONTHS,
                "max_bedroom_gap": MAX_BEDROOM_GAP,
                "max_area_ratio": MAX_AREA_RATIO,
                "min_concept_overlap": MIN_CONCEPT_OVERLAP,
            },
            "ground_truth_source": (
                "Concept tags assigned during fixture generation. The retrieval pipeline "
                "reads only the rendered listing prose and never sees these tags."
            ),
        },
        "cases": cases,
    }


def main() -> None:
    dataset = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(dataset, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    by_kind: dict[str, list[dict]] = {}
    for case in dataset["cases"]:
        by_kind.setdefault(case["kind"], []).append(case)
    for kind, cases in by_kind.items():
        gradeable = [case for case in cases if case["relevant_ids"]]
        print(f"\n{kind}: {len(cases)} cases, {len(gradeable)} gradeable")
        for case in cases[:6]:
            print(
                f"  {case['target_address'][:44]:<46} "
                f"relevant={len(case['relevant_ids']):>2}  near-miss={case['near_miss_count']:>2}"
            )
        if len(cases) > 6:
            print(f"  ... and {len(cases) - 6} more")
    total_relevant = sum(len(case["relevant_ids"]) for case in dataset["cases"])
    print(f"\ntotal cases={len(dataset['cases'])}  total relevance labels={total_relevant}")


if __name__ == "__main__":
    main()
