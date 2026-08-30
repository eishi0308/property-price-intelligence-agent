#!/usr/bin/env python3
"""Generate the deterministic demo corpus used by `DemoProvider`.

WHY THIS EXISTS
---------------
The application must be runnable with no paid property-data credentials, and the
retrieval evaluation suite needs ground truth. Both are satisfied by a synthetic
but *realistic* corpus whose generative process we know exactly.

DESIGN CHOICE THAT MATTERS
--------------------------
Every qualitative concept (north-facing, renovated, parking, quiet position, …)
is expressed in a listing in one of two registers, chosen deterministically:

  * ``explicit``  — literally contains the searchable term ("north-facing",
                    "secure parking"). PostgreSQL full-text retrieval finds these.
  * ``implicit``  — paraphrased with no shared keyword ("bathed in morning sun",
                    "lock-up garaging on title"). Only dense/semantic retrieval
                    has a chance.

Because the split is roughly even, neither the keyword arm nor the vector arm can
recover the full comparable set alone. The hybrid-fusion evaluation therefore
measures something real instead of confirming a foregone conclusion.

Output is written to ``app/providers/fixtures/demo_dataset.json`` and is
byte-stable for a given SEED so it can be committed and diffed.

NOTE: this is data *synthesis* for fixtures. It is not, and is never used as, a
predictive or statistical valuation model — the product derives price evidence
only from observed sold prices in the corpus.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SEED = 20260830
TODAY = date(2026, 8, 30)
OUTPUT = (
    Path(__file__).resolve().parents[1] / "app" / "providers" / "fixtures" / "demo_dataset.json"
)

# --------------------------------------------------------------------------------------
# Geography — inner-west Sydney, real suburb centroids, synthetic street addresses.
# --------------------------------------------------------------------------------------
SUBURBS: list[dict[str, Any]] = [
    {"suburb": "Strathfield", "postcode": "2135", "lat": -33.8730, "lon": 151.0950, "tier": 1.00},
    {
        "suburb": "North Strathfield",
        "postcode": "2137",
        "lat": -33.8570,
        "lon": 151.0870,
        "tier": 0.95,
    },
    {"suburb": "Homebush", "postcode": "2140", "lat": -33.8650, "lon": 151.0830, "tier": 0.92},
    {"suburb": "Burwood", "postcode": "2134", "lat": -33.8770, "lon": 151.1040, "tier": 0.96},
    {"suburb": "Concord", "postcode": "2137", "lat": -33.8560, "lon": 151.1030, "tier": 1.05},
    {"suburb": "Croydon", "postcode": "2132", "lat": -33.8830, "lon": 151.1140, "tier": 0.93},
    {"suburb": "Ashfield", "postcode": "2131", "lat": -33.8890, "lon": 151.1250, "tier": 0.85},
    {"suburb": "Flemington", "postcode": "2140", "lat": -33.8700, "lon": 151.0700, "tier": 0.80},
]

STREETS = [
    "Albert Road",
    "Redmyre Road",
    "Beresford Road",
    "Chalmers Road",
    "Homebush Road",
    "Mosely Street",
    "The Boulevarde",
    "Wentworth Road",
    "Everton Road",
    "Nicholson Street",
    "Burlington Road",
    "Bridge Road",
    "Parramatta Road",
    "Elva Street",
    "Meredith Street",
    "Broughton Street",
    "Cornelia Street",
    "Gladstone Street",
    "Correys Avenue",
    "Yandarlo Street",
]

# --------------------------------------------------------------------------------------
# Qualitative concepts. `explicit` phrasings share keywords with the query; `implicit`
# phrasings deliberately do not.
# --------------------------------------------------------------------------------------
CONCEPTS: dict[str, dict[str, Any]] = {
    "north_facing": {
        "premium": 0.030,
        "explicit": [
            "north-facing living areas",
            "a true north-facing aspect",
            "north facing balcony capturing the sun",
        ],
        "implicit": [
            "living areas bathed in morning sun year round",
            "an enviable northerly orientation drenched in natural warmth",
            "sun tracks across the main living space all afternoon",
        ],
    },
    "renovated": {
        "premium": 0.055,
        "explicit": [
            "fully renovated throughout",
            "a recently renovated kitchen and bathroom",
            "renovated to a high standard",
        ],
        "implicit": [
            "reimagined from top to bottom by its current owners",
            "a considered contemporary update with stone benchtops and new joinery",
            "freshly presented with all the hard work already done",
        ],
    },
    "parking": {
        "premium": 0.045,
        "explicit": [
            "secure parking for one vehicle",
            "secure car space on title",
            "secure undercover parking",
        ],
        "implicit": [
            "lock-up garaging accessed via the basement",
            "off-street accommodation for the family car",
            "a dedicated space behind remote-controlled gates",
        ],
    },
    "balcony": {
        "premium": 0.025,
        "explicit": [
            "an oversized balcony",
            "a generous entertainers balcony",
            "a wide covered balcony off the living room",
        ],
        "implicit": [
            "generous outdoor entertaining space flowing from the living room",
            "a sheltered alfresco terrace made for weekend gatherings",
            "an outdoor room that doubles the living area in summer",
        ],
    },
    "quiet_position": {
        "premium": 0.030,
        "explicit": [
            "a quiet position away from traffic",
            "quiet rear aspect",
            "set in a quiet tree-lined street",
        ],
        "implicit": [
            "a peaceful rear setting insulated from the street",
            "a tranquil pocket where birdsong replaces traffic",
            "tucked away from through-traffic in a whisper-still enclave",
        ],
    },
    "natural_light": {
        "premium": 0.020,
        "explicit": [
            "flooded with natural light",
            "abundant natural light from oversized windows",
            "a light-filled interior",
        ],
        "implicit": [
            "sun-drenched interiors from dawn until dusk",
            "wall-to-wall glazing that lifts every room",
            "a bright, airy feel courtesy of double-height glass",
        ],
    },
    "top_floor": {
        "premium": 0.025,
        "explicit": ["top floor position", "positioned on the top floor", "top-floor residence"],
        "implicit": [
            "the uppermost level of the building with nobody above",
            "an elevated crown position within the block",
            "the highest tier of the complex, free of overhead neighbours",
        ],
    },
    "district_views": {
        "premium": 0.040,
        "explicit": ["district views", "sweeping district views", "leafy district outlook"],
        "implicit": [
            "an outlook stretching across the treetops to the horizon",
            "uninterrupted vistas over the surrounding rooftops",
            "a green panorama from every principal window",
        ],
    },
    "courtyard": {
        "premium": 0.030,
        "explicit": [
            "a private courtyard",
            "a paved courtyard garden",
            "a low-maintenance courtyard",
        ],
        "implicit": [
            "a walled garden retreat for morning coffee",
            "an enclosed outdoor sanctuary wrapped in greenery",
            "a sheltered ground-level garden space",
        ],
    },
    "modern_building": {
        "premium": 0.020,
        "explicit": ["in a modern security building", "a contemporary boutique complex"],
        "implicit": [
            "part of a recently completed development with lift access",
            "a newly built block of only a handful of residences",
        ],
    },
    # --- Negative concepts: these depress price and must be visible to the reranker. ---
    "main_road": {
        "premium": -0.055,
        "explicit": ["fronting a main road", "on a busy main road", "main road frontage"],
        "implicit": [
            "positioned on a well-connected arterial route",
            "addressing one of the area's busier thoroughfares",
        ],
    },
    "original_condition": {
        "premium": -0.070,
        "explicit": [
            "original condition throughout",
            "in original condition awaiting renovation",
            "unrenovated and ready for your touch",
        ],
        "implicit": [
            "a blank canvas for the renovator with scope to add value",
            "held by the same family for decades and awaiting modernisation",
            "presenting an opportunity to update to your own taste",
        ],
    },
    "ground_floor": {
        "premium": -0.015,
        "explicit": ["ground floor apartment", "on the ground floor"],
        "implicit": ["street-level access with no stairs to climb"],
    },
    "small_block": {
        "premium": -0.020,
        "explicit": ["compact floorplan"],
        "implicit": ["an efficient layout that makes the most of every metre"],
    },
}

POSITIVE_CONCEPTS = [k for k, v in CONCEPTS.items() if v["premium"] > 0]
NEGATIVE_CONCEPTS = [k for k, v in CONCEPTS.items() if v["premium"] < 0]

APARTMENT_ONLY = {"top_floor", "ground_floor", "modern_building", "balcony"}
HOUSE_ONLY = {"courtyard"}

# Every opener must end with "this" so `{opener} {beds}-bedroom {noun} delivers ...`
# reads as a grammatical sentence.
OPENERS = [
    "Perfectly positioned in one of the area's most sought-after pockets, this",
    "Offered to the market for the first time in years, this",
    "A rare opportunity in a tightly held pocket, this",
    "Combining space, comfort and convenience, this",
    "Set within a short stroll of the station and village shops, this",
    "Designed for effortless everyday living, this",
]

CLOSERS = [
    "Walk to the station, local schools and village cafes.",
    "Moments from parklands, transport and shopping.",
    "An easy commute to the CBD with the local village at your door.",
    "Close to quality schooling, transport links and everyday amenities.",
    "Convenient access to major arterials and the rail corridor.",
]

BASE_PRICE = {
    ("apartment", 1): 620_000,
    ("apartment", 2): 880_000,
    ("apartment", 3): 1_180_000,
    ("townhouse", 2): 1_050_000,
    ("townhouse", 3): 1_390_000,
    ("townhouse", 4): 1_620_000,
    ("house", 3): 1_820_000,
    ("house", 4): 2_250_000,
    ("house", 5): 2_720_000,
}


def _pick_concepts(rng: random.Random, property_type: str) -> list[str]:
    pool = list(POSITIVE_CONCEPTS)
    if property_type == "apartment":
        pool = [c for c in pool if c not in HOUSE_ONLY]
    else:
        pool = [c for c in pool if c not in APARTMENT_ONLY]
    rng.shuffle(pool)
    chosen = pool[: rng.randint(2, 4)]
    negative_pool = [
        c for c in NEGATIVE_CONCEPTS if not (property_type != "apartment" and c in APARTMENT_ONLY)
    ]
    if rng.random() < 0.35:
        chosen.append(rng.choice(negative_pool))
    if "renovated" in chosen and "original_condition" in chosen:
        chosen.remove("original_condition")
    return chosen


def _describe(
    rng: random.Random, property_type: str, beds: int, baths: int, cars: int, concepts: list[str]
) -> tuple[str, str, dict[str, str]]:
    """Return (headline, description, {concept: register})."""
    registers: dict[str, str] = {}
    fragments: list[str] = []
    for concept in concepts:
        register = "explicit" if rng.random() < 0.5 else "implicit"
        registers[concept] = register
        fragments.append(rng.choice(CONCEPTS[concept][register]))
    rng.shuffle(fragments)

    noun = {"apartment": "apartment", "townhouse": "townhouse", "house": "family home"}[
        property_type
    ]
    opener = rng.choice(OPENERS)
    body = (
        f"{opener} {beds}-bedroom {noun} delivers {fragments[0]}"
        + (f", {fragments[1]}" if len(fragments) > 1 else "")
        + (f" and {fragments[2]}" if len(fragments) > 2 else "")
        + "."
    )
    if len(fragments) > 3:
        body += f" It further offers {fragments[3]}."
    body += (
        f" Accommodation comprises {beds} bedrooms and {baths} bathroom"
        f"{'s' if baths != 1 else ''}"
        + (
            f" with {cars} car space{'s' if cars != 1 else ''}."
            if cars
            else " with no dedicated parking."
        )
    )
    body += " " + rng.choice(CLOSERS)

    headline_bits = [f"{beds} Bed {property_type.title()}"]
    if "renovated" in concepts:
        headline_bits.insert(0, "Renovated")
    elif "original_condition" in concepts:
        headline_bits.insert(0, "Renovator's Opportunity:")
    if "north_facing" in concepts:
        headline_bits.append("With Northerly Aspect")
    return " ".join(headline_bits), body, registers


def _price(
    rng: random.Random,
    suburb: dict[str, Any],
    property_type: str,
    beds: int,
    area: float,
    concepts: list[str],
    sold_at: date,
) -> int:
    base = BASE_PRICE.get((property_type, beds), 900_000)
    price = base * suburb["tier"]
    typical_area = {
        "apartment": 55 + 25 * beds,
        "townhouse": 90 + 30 * beds,
        "house": 110 + 35 * beds,
    }[property_type]
    price *= 1 + 0.35 * ((area - typical_area) / typical_area)
    for concept in concepts:
        price *= 1 + CONCEPTS[concept]["premium"]
    months_ago = (TODAY - sold_at).days / 30.44
    price *= 1 - 0.0022 * months_ago  # gentle recency drift, fixture realism only
    price *= rng.uniform(0.965, 1.035)  # idiosyncratic transaction noise
    return int(round(price / 5_000) * 5_000)


def _jitter_coords(rng: random.Random, suburb: dict[str, Any]) -> tuple[float, float]:
    return (
        round(suburb["lat"] + rng.uniform(-0.011, 0.011), 6),
        round(suburb["lon"] + rng.uniform(-0.013, 0.013), 6),
    )


def _make_property(
    rng: random.Random, idx: int, *, property_type: str, suburb: dict[str, Any]
) -> dict[str, Any]:
    if property_type == "apartment":
        beds = rng.choices([1, 2, 3], weights=[0.2, 0.6, 0.2])[0]
        baths = 1 if beds < 3 else rng.choice([1, 2])
        cars = rng.choices([0, 1, 2], weights=[0.18, 0.7, 0.12])[0]
        floor_area = round(rng.uniform(46, 68) + 22 * beds, 1)
        land_area = None
    elif property_type == "townhouse":
        beds = rng.choices([2, 3, 4], weights=[0.25, 0.55, 0.2])[0]
        baths = rng.choice([1, 2, 2])
        cars = rng.choices([1, 2], weights=[0.6, 0.4])[0]
        floor_area = round(rng.uniform(85, 120) + 25 * beds, 1)
        land_area = round(rng.uniform(120, 240), 1)
    else:
        beds = rng.choices([3, 4, 5], weights=[0.35, 0.45, 0.2])[0]
        baths = rng.choice([1, 2, 2, 3])
        cars = rng.choices([1, 2, 3], weights=[0.3, 0.55, 0.15])[0]
        floor_area = round(rng.uniform(120, 190) + 20 * beds, 1)
        land_area = round(rng.uniform(320, 720), 1)

    concepts = _pick_concepts(rng, property_type)
    lat, lon = _jitter_coords(rng, suburb)
    number = rng.randint(1, 180)
    unit = f"{rng.randint(1, 24)}/" if property_type == "apartment" else ""
    street = rng.choice(STREETS)
    address = f"{unit}{number} {street}, {suburb['suburb']} {suburb['postcode']}"

    headline, description, registers = _describe(rng, property_type, beds, baths, cars, concepts)
    return {
        "external_id": f"DEMO-P-{idx:04d}",
        "address": address,
        "suburb": suburb["suburb"],
        "state": "NSW",
        "postcode": suburb["postcode"],
        "latitude": lat,
        "longitude": lon,
        "property_type": property_type,
        "bedrooms": beds,
        "bathrooms": baths,
        "carspaces": cars,
        "floor_area_sqm": floor_area,
        "land_area_sqm": land_area,
        "year_built": rng.choice([None, rng.randint(1955, 2022)]),
        "_concepts": concepts,
        "_concept_registers": registers,
        "_headline": headline,
        "_description": description,
    }


def _build() -> dict[str, Any]:
    rng = random.Random(SEED)
    properties: list[dict[str, Any]] = []
    listings: list[dict[str, Any]] = []
    idx = 0

    # ---- Sold corpus -------------------------------------------------------------
    type_plan = ["apartment"] * 92 + ["townhouse"] * 34 + ["house"] * 48
    rng.shuffle(type_plan)
    for property_type in type_plan:
        idx += 1
        suburb = rng.choices(SUBURBS, weights=[0.22, 0.11, 0.12, 0.14, 0.12, 0.11, 0.10, 0.08])[0]
        record = _make_property(rng, idx, property_type=property_type, suburb=suburb)
        days_ago = int(rng.triangular(10, 700, 150))
        sold_at = TODAY - timedelta(days=days_ago)
        listed_at = sold_at - timedelta(days=rng.randint(21, 60))
        area = (
            record["floor_area_sqm"]
            if property_type == "apartment"
            else (record["land_area_sqm"] or record["floor_area_sqm"])
        )
        norm_area = record["floor_area_sqm"]
        sold_price = _price(
            rng, suburb, property_type, record["bedrooms"], norm_area, record["_concepts"], sold_at
        )
        properties.append(record)
        listings.append(
            {
                "external_listing_id": f"DEMO-L-{idx:04d}",
                "property_external_id": record["external_id"],
                "status": "sold",
                "asking_price": None,
                "price_guide_text": None,
                "headline": record["_headline"],
                "description": record["_description"],
                "listed_at": listed_at.isoformat(),
                "sold_at": sold_at.isoformat(),
                "sold_price": sold_price,
                "source": "demo_fixture",
                "source_url": f"https://demo.invalid/sold/{record['external_id']}",
            }
        )
        _ = area

    # ---- On-market targets -------------------------------------------------------
    targets: list[dict[str, Any]] = []
    target_specs = [
        {
            "suburb": SUBURBS[0],
            "property_type": "apartment",
            "beds": 2,
            "baths": 2,
            "cars": 1,
            "floor_area": 96.0,
            "land_area": None,
            "concepts": ["renovated", "north_facing", "parking", "balcony", "quiet_position"],
            "address": "12/45 Redmyre Road, Strathfield 2135",
            "asking_price": 950_000,
            "price_guide_text": "Guide $950,000",
            "aliases": [
                "https://www.realestate.com.au/property-apartment-nsw-strathfield-145820394",
                "12/45 Redmyre Road, Strathfield NSW 2135",
                "12/45 redmyre rd strathfield",
            ],
        },
        {
            "suburb": SUBURBS[4],
            "property_type": "house",
            "beds": 4,
            "baths": 2,
            "cars": 2,
            "floor_area": 198.0,
            "land_area": 556.0,
            "concepts": ["original_condition", "quiet_position", "district_views"],
            "address": "27 Gladstone Street, Concord 2137",
            "asking_price": 2_450_000,
            "price_guide_text": "Guide $2,450,000",
            "aliases": [
                "https://www.realestate.com.au/property-house-nsw-concord-145820511",
                "27 Gladstone Street, Concord NSW 2137",
            ],
        },
        {
            "suburb": SUBURBS[3],
            "property_type": "townhouse",
            "beds": 3,
            "baths": 2,
            "cars": 2,
            "floor_area": 168.0,
            "land_area": 190.0,
            "concepts": ["renovated", "courtyard", "natural_light", "parking"],
            "address": "5/8 Everton Road, Burwood 2134",
            "asking_price": 1_395_000,
            "price_guide_text": "Guide $1,395,000",
            "aliases": [
                "https://www.domain.com.au/5-8-everton-road-burwood-nsw-2134-2019887654",
                "5/8 Everton Road, Burwood NSW 2134",
            ],
        },
    ]

    for spec in target_specs:
        idx += 1
        suburb = spec["suburb"]
        lat, lon = _jitter_coords(rng, suburb)
        headline, description, registers = _describe(
            rng, spec["property_type"], spec["beds"], spec["baths"], spec["cars"], spec["concepts"]
        )
        record = {
            "external_id": f"DEMO-T-{idx:04d}",
            "address": spec["address"],
            "suburb": suburb["suburb"],
            "state": "NSW",
            "postcode": suburb["postcode"],
            "latitude": lat,
            "longitude": lon,
            "property_type": spec["property_type"],
            "bedrooms": spec["beds"],
            "bathrooms": spec["baths"],
            "carspaces": spec["cars"],
            "floor_area_sqm": spec["floor_area"],
            "land_area_sqm": spec["land_area"],
            "year_built": None,
            "_concepts": spec["concepts"],
            "_concept_registers": registers,
            "_headline": headline,
            "_description": description,
        }
        properties.append(record)
        listings.append(
            {
                "external_listing_id": f"DEMO-CL-{idx:04d}",
                "property_external_id": record["external_id"],
                "status": "current",
                "asking_price": spec["asking_price"],
                "price_guide_text": spec["price_guide_text"],
                "headline": headline,
                "description": description,
                "listed_at": (TODAY - timedelta(days=rng.randint(7, 30))).isoformat(),
                "sold_at": None,
                "sold_price": None,
                "source": "demo_fixture",
                "source_url": f"https://demo.invalid/listing/{record['external_id']}",
            }
        )
        targets.append({"external_id": record["external_id"], "aliases": spec["aliases"]})

    # ---- Suburb market context (evidence, not prediction) -------------------------
    market: list[dict[str, Any]] = []
    for suburb in SUBURBS:
        for property_type in ("apartment", "house", "townhouse"):
            sold = [
                listing
                for listing, prop in zip(listings, properties, strict=False)
                if prop["suburb"] == suburb["suburb"]
                and prop["property_type"] == property_type
                and listing["sold_price"]
            ]
            if len(sold) < 3:
                continue
            prices = sorted(item["sold_price"] for item in sold)
            median = prices[len(prices) // 2]
            market.append(
                {
                    "suburb": suburb["suburb"],
                    "state": "NSW",
                    "postcode": suburb["postcode"],
                    "property_type": property_type,
                    "median_sold_price": int(round(median / 5_000) * 5_000),
                    "median_price_period": "12 months to Aug 2026",
                    "sales_volume_12m": len(
                        [
                            s
                            for s in sold
                            if s["sold_at"] >= (TODAY - timedelta(days=365)).isoformat()
                        ]
                    ),
                    "median_days_on_market": rng.randint(21, 48),
                    "price_change_12m_pct": round(rng.uniform(-3.5, 7.5), 1),
                    "commentary": (
                        f"{property_type.title()} sales in {suburb['suburb']} over the 12 months to "
                        f"August 2026 were concentrated between "
                        f"${prices[len(prices)//4]:,} and ${prices[3*len(prices)//4]:,}. "
                        f"Volume was {'steady' if len(sold) > 8 else 'thin'} at {len(sold)} recorded sales "
                        f"in this dataset, so individual results carry more weight than the median alone."
                    ),
                    "source": "demo_fixture",
                    "as_at": TODAY.isoformat(),
                }
            )

    # ---- Location context ---------------------------------------------------------
    location: list[dict[str, Any]] = []
    for suburb in SUBURBS:
        location.append(
            {
                "suburb": suburb["suburb"],
                "state": "NSW",
                "postcode": suburb["postcode"],
                "content": (
                    f"{suburb['suburb']} sits on the inner-western rail corridor with direct services "
                    f"to Sydney's CBD. The suburb is characterised by a mix of post-war walk-up blocks, "
                    f"newer boutique developments near the station, and Federation-era housing on the "
                    f"quieter southern streets. Proximity to the station and to arterial roads is the "
                    f"single largest intra-suburb price differentiator observed in this dataset: "
                    f"stock fronting main roads consistently transacts below equivalent stock on "
                    f"side streets."
                ),
                "source": "demo_fixture",
                "as_at": TODAY.isoformat(),
            }
        )

    return {
        "_meta": {
            "generator": "scripts/generate_fixtures.py",
            "seed": SEED,
            "generated_for_date": TODAY.isoformat(),
            "warning": (
                "SYNTHETIC DEMO DATA. Addresses, prices, sale dates and descriptions are "
                "fabricated for development and evaluation. They do not describe any real "
                "Australian property and must never be presented as live market data."
            ),
        },
        "properties": properties,
        "listings": listings,
        "targets": targets,
        "market_context": market,
        "location_context": location,
    }


def main() -> None:
    data = _build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    sold = [listing for listing in data["listings"] if listing["status"] == "sold"]
    print(
        f"Wrote {OUTPUT.relative_to(Path.cwd()) if OUTPUT.is_relative_to(Path.cwd()) else OUTPUT}"
    )
    print(f"  properties       : {len(data['properties'])}")
    print(f"  sold listings    : {len(sold)}")
    print(f"  on-market targets: {len(data['targets'])}")
    print(f"  market rows      : {len(data['market_context'])}")
    prices = sorted(item["sold_price"] for item in sold)
    print(f"  sold price range : ${prices[0]:,} - ${prices[-1]:,}")


if __name__ == "__main__":
    main()
