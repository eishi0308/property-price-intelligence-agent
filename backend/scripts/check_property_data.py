#!/usr/bin/env python3
"""PHASE 1 GATE — property data feasibility.

Answers one question before any further engineering effort is spent:

    Can the configured provider actually return the data this product needs?

The critical finding is not pass/fail on individual calls — it is whether
*historical listing description text* exists in enough volume to make semantic
comparable retrieval meaningful. Without that corpus, embeddings and pgvector
would be theatre. This script says so plainly rather than letting a weak
retrieval stage hide behind impressive architecture.

Usage:
    python scripts/check_property_data.py
    python scripts/check_property_data.py --query "12/45 Redmyre Road, Strathfield NSW 2135"
    python scripts/check_property_data.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.providers import build_provider
from app.providers.base import PropertyDataProvider, ProviderError
from app.schemas.analysis import DataFeasibilityReport, FeasibilityCheck

DEFAULT_QUERY = "12/45 Redmyre Road, Strathfield NSW 2135"

# Below this, a semantic index cannot distinguish comparables from noise.
MIN_DESCRIPTIONS_FOR_VECTOR = 20
MIN_DESCRIPTION_RATIO = 0.40


def _excerpt(text: str | None, limit: int = 110) -> str | None:
    if not text:
        return None
    flat = " ".join(text.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


async def run_feasibility(query: str, provider: PropertyDataProvider) -> DataFeasibilityReport:
    settings = get_settings()
    checks: list[FeasibilityCheck] = []
    notes: list[str] = []
    capabilities = provider.capabilities

    if capabilities.is_demo:
        notes.append(
            "PROVIDER IS DEMO. Every value below comes from a synthetic fixture corpus. "
            "This report proves the pipeline works; it proves nothing about a live vendor."
        )

    # --- 1. Target property ---------------------------------------------------
    resolution = None
    target = None
    try:
        resolution = await provider.resolve_property(query)
        target = resolution.property_record
        checks.append(
            FeasibilityCheck(
                name="Target property",
                passed=bool(target),
                detail=(
                    f"Resolved to {target.address}"
                    if target
                    else f"Not resolved: {resolution.failure_reason}"
                ),
                sample=target.external_id if target else None,
            )
        )
    except ProviderError as exc:
        checks.append(
            FeasibilityCheck(
                name="Target property", passed=False, detail=f"{type(exc).__name__}: {exc}"
            )
        )

    # --- 2. Property attributes ----------------------------------------------
    if target:
        present = {
            "bedrooms": target.bedrooms,
            "bathrooms": target.bathrooms,
            "carspaces": target.carspaces,
            "floor_area": target.floor_area_sqm,
            "land_area": target.land_area_sqm,
            "coordinates": target.coordinates,
        }
        known = [key for key, value in present.items() if value is not None]
        missing = [key for key, value in present.items() if value is None]
        # Coordinates are non-negotiable: without them there is no geographic filter.
        checks.append(
            FeasibilityCheck(
                name="Property attributes",
                passed=target.coordinates is not None and len(known) >= 4,
                detail=(
                    f"{len(known)}/6 attributes present"
                    + (f"; missing: {', '.join(missing)}" if missing else "")
                    + (
                        ""
                        if target.coordinates
                        else "; NO COORDINATES — geographic filtering impossible"
                    )
                ),
                sample=f"{target.property_type.value}, {target.bedrooms}b/{target.bathrooms}b/{target.carspaces}c",
            )
        )
    else:
        checks.append(
            FeasibilityCheck(
                name="Property attributes", passed=False, detail="No target property to inspect."
            )
        )

    # --- 3. Current listing & asking price ------------------------------------
    current = None
    if target:
        try:
            current = await provider.get_current_listing(target.external_id)
        except ProviderError as exc:
            notes.append(f"get_current_listing failed: {exc}")
    checks.append(
        FeasibilityCheck(
            name="Current listing",
            passed=current is not None,
            detail="Live listing returned"
            if current
            else "No current listing (property may be off-market).",
            sample=_excerpt(current.headline if current else None),
        )
    )
    has_price = bool(current and (current.asking_price or current.price_guide_text))
    checks.append(
        FeasibilityCheck(
            name="Asking / guide price",
            passed=has_price,
            detail=(
                "Asking price available"
                if current and current.asking_price
                else "Only a text price guide is available; the user must confirm a number"
                if current and current.price_guide_text
                else "No price published — the API must accept a user-supplied asking price."
            ),
            sample=(
                f"${current.asking_price:,}"
                if current and current.asking_price
                else (current.price_guide_text if current else None)
            ),
        )
    )

    # --- 4. Nearby sold transactions ------------------------------------------
    sold: list = []
    if target and target.coordinates:
        try:
            sold = await provider.search_sold_transactions(
                centre_latitude=target.coordinates.latitude,
                centre_longitude=target.coordinates.longitude,
                radius_km=settings.default_radius_km,
                sold_since=date.today() - timedelta(days=30 * settings.default_lookback_months),
                limit=200,
            )
        except ProviderError as exc:
            notes.append(f"search_sold_transactions failed: {exc}")
    checks.append(
        FeasibilityCheck(
            name="Sold transactions",
            passed=len(sold) >= 10,
            detail=(
                f"{len(sold)} sales within {settings.default_radius_km} km over "
                f"{settings.default_lookback_months} months"
                + ("" if len(sold) >= 10 else " — too few for defensible comparison at this radius")
            ),
            sample=f"nearest {sold[0].distance_km} km" if sold else None,
        )
    )
    checks.append(
        FeasibilityCheck(
            name="Sold property identifiers",
            passed=bool(sold) and all(item.external_id for item in sold),
            detail=(
                "Every sale carries a stable property id (required to join listing history)."
                if sold and all(item.external_id for item in sold)
                else "Sales are missing stable identifiers; historical listings cannot be joined."
            ),
            sample=sold[0].external_id if sold else None,
        )
    )
    checks.append(
        FeasibilityCheck(
            name="Historical sale prices",
            passed=bool(sold) and all(item.sold_price > 0 for item in sold),
            detail=(
                f"All {len(sold)} sales carry a numeric sold price."
                if sold and all(item.sold_price > 0 for item in sold)
                else "Some sales have no disclosed price; those rows are dropped, not estimated."
            ),
            sample=f"${sold[0].sold_price:,} on {sold[0].sold_at}" if sold else None,
        )
    )

    # --- 5. THE DECISIVE CHECK: historical listing descriptions ----------------
    described = 0
    sample_description = None
    inspect = sold[:40]
    for transaction in inspect:
        listing = transaction.listing
        if listing is None:
            try:
                listing = await provider.get_historical_listing(transaction.external_id)
            except ProviderError:
                listing = None
        if listing and listing.has_qualitative_text:
            described += 1
            sample_description = sample_description or _excerpt(listing.description, 150)

    ratio = described / len(inspect) if inspect else 0.0
    descriptions_ok = described >= MIN_DESCRIPTIONS_FOR_VECTOR and ratio >= MIN_DESCRIPTION_RATIO
    checks.append(
        FeasibilityCheck(
            name="Historical descriptions",
            passed=descriptions_ok,
            detail=(
                f"{described}/{len(inspect)} inspected sales ({ratio:.0%}) carry usable "
                f"qualitative text (>=80 chars)."
                + (
                    ""
                    if descriptions_ok
                    else f" Need >={MIN_DESCRIPTIONS_FOR_VECTOR} and >={MIN_DESCRIPTION_RATIO:.0%}."
                )
            ),
            sample=sample_description,
        )
    )

    # --- 6. Market context ----------------------------------------------------
    market = None
    if target:
        try:
            market = await provider.get_market_context(
                suburb=target.suburb,
                state=target.state,
                postcode=target.postcode,
                property_type=target.property_type,
            )
        except ProviderError as exc:
            notes.append(f"get_market_context failed: {exc}")
    checks.append(
        FeasibilityCheck(
            name="Market context",
            passed=market is not None,
            detail=(
                "Suburb market context available as supporting evidence."
                if market
                else "No market context — the RAG market-evidence domain will be empty."
            ),
            sample=(
                f"median ${market.median_sold_price:,}"
                if market and market.median_sold_price
                else None
            ),
        )
    )

    # --- Verdict on vector retrieval ------------------------------------------
    if not sold:
        viable, reason = (
            "NO",
            "No sold transactions were retrievable, so there is nothing to index.",
        )
    elif descriptions_ok:
        viable, reason = "YES", None
    elif described > 0:
        viable = "LIMITED"
        reason = (
            f"Insufficient qualitative historical text: only {described} of {len(inspect)} "
            f"inspected sales ({ratio:.0%}) have a usable description. Semantic retrieval will "
            f"fall back to structured-attribute text, which cannot distinguish "
            f"'renovated, north-facing, quiet' from its opposite."
        )
    else:
        viable = "NO"
        reason = (
            "No historical listing descriptions are available from this provider. Embedding "
            "structured attributes alone duplicates what SQL already does deterministically, "
            "so vector retrieval would add cost and no signal."
        )

    if settings.resolved_embedding_backend.value == "offline" and viable == "YES":
        notes.append(
            "Corpus supports semantic retrieval, but EMBEDDING_BACKEND resolved to 'offline' "
            "(no OPENAI_API_KEY). The deterministic offline encoder matches concept lexicon "
            "terms, not true semantics — recall on unseen paraphrases will be materially lower."
        )

    return DataFeasibilityReport(
        provider=capabilities.name,
        provider_is_demo=capabilities.is_demo,
        generated_at=datetime.now(UTC),
        checks=checks,
        vector_retrieval_viable=viable,
        vector_retrieval_reason=reason,
        embedding_backend=settings.resolved_embedding_backend.value,
        notes=notes,
    )


def render(report: DataFeasibilityReport) -> str:
    width = 62
    lines = ["", "DATA FEASIBILITY REPORT", "=" * width, ""]
    lines.append(
        f"Provider                    {report.provider}{'  (DEMO FIXTURES)' if report.provider_is_demo else ''}"
    )
    lines.append(f"Embedding backend           {report.embedding_backend}")
    lines.append(f"Generated                   {report.generated_at:%Y-%m-%d %H:%M:%S} UTC")
    lines.append("")
    for check in report.checks:
        lines.append(f"{check.name:<27} {'PASS' if check.passed else 'FAIL'}")
        lines.append(f"    {check.detail}")
        if check.sample:
            lines.append(f"    e.g. {check.sample}")
    lines.append("")
    lines.append("-" * width)
    lines.append(f"VECTOR RETRIEVAL VIABLE     {report.vector_retrieval_viable}")
    if report.vector_retrieval_reason:
        lines.append(f"Reason: {report.vector_retrieval_reason}")
    lines.append("-" * width)
    if report.notes:
        lines.append("")
        lines.append("NOTES")
        for note in report.notes:
            lines.append(f"  * {note}")
    lines.append("")
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    provider = build_provider()
    try:
        report = await run_feasibility(args.query, provider)
    finally:
        await provider.close()
    print(json.dumps(report.model_dump(mode="json"), indent=2) if args.json else render(report))
    # Exit non-zero when the gate genuinely fails, so CI can block on it.
    return 0 if report.all_passed or args.no_fail else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify property-data feasibility for the configured provider."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Address or property URL to probe.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--no-fail", action="store_true", help="Always exit 0 (reporting mode).")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
