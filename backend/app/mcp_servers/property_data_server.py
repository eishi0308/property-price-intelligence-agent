#!/usr/bin/env python3
"""MCP server: external property-data capabilities.

This is the boundary between the agent and the outside world. Everything that
reaches a third-party property vendor goes through a tool declared here, which
means:

* the agent's access to vendor data is enumerable and reviewable in one place;
* swapping PropTrack for Domain changes nothing on the agent side;
* the same capabilities are available to any other MCP client (an IDE, a
  different agent) without importing this codebase.

Run standalone:
    python -m app.mcp_servers.property_data_server
"""

from __future__ import annotations

import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.mcp_servers import tools_impl
from app.observability import configure_logging

mcp = FastMCP(
    "property-data",
    instructions=(
        "Australian property data: address resolution, property attributes, current and "
        "historical listings, nearby settled sales, and suburb market context. All values "
        "originate from the configured data provider. Records carrying is_demo_data=true "
        "are synthetic fixtures and must never be presented as live market data."
    ),
)


@mcp.tool()
async def resolve_property(query: str) -> dict[str, Any]:
    """Resolve a property URL (realestate.com.au, domain.com.au) or an Australian
    street address to a single known property.

    Args:
        query: A property listing URL or a full street address.
    """
    return await tools_impl.resolve_property(query)


@mcp.tool()
async def get_property_details(external_id: str) -> dict[str, Any]:
    """Return recorded structural attributes for one property.

    Args:
        external_id: Provider property identifier from `resolve_property`.
    """
    return await tools_impl.get_property_details(external_id)


@mcp.tool()
async def get_current_listing(external_id: str) -> dict[str, Any]:
    """Return the live listing (asking price, guide text, description) if on market.

    Args:
        external_id: Provider property identifier.
    """
    return await tools_impl.get_current_listing(external_id)


@mcp.tool()
async def get_historical_listing(external_id: str) -> dict[str, Any]:
    """Return the listing as marketed at the time of the property's most recent sale.

    This is the source of the qualitative text that makes semantic comparison possible.

    Args:
        external_id: Provider property identifier.
    """
    return await tools_impl.get_historical_listing(external_id)


@mcp.tool()
async def search_sold_properties(
    latitude: float,
    longitude: float,
    radius_km: float = 3.0,
    lookback_months: int = 12,
    property_types: list[str] | None = None,
    min_bedrooms: int | None = None,
    max_bedrooms: int | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Find recent settled sales near a point and persist them for retrieval.

    Increase radius_km or lookback_months when too few comparable sales were found.

    Args:
        latitude: Centre latitude (the target property).
        longitude: Centre longitude.
        radius_km: Search radius in kilometres; capped by server configuration.
        lookback_months: How far back to include settled sales.
        property_types: Restrict to these types, e.g. ["apartment"].
        min_bedrooms: Lower bedroom bound.
        max_bedrooms: Upper bedroom bound.
        limit: Maximum sales to return.
    """
    return await tools_impl.search_sold_properties(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        lookback_months=lookback_months,
        property_types=property_types,
        min_bedrooms=min_bedrooms,
        max_bedrooms=max_bedrooms,
        limit=limit,
    )


@mcp.tool()
async def get_market_context(
    suburb: str, state: str, postcode: str, property_type: str | None = None
) -> dict[str, Any]:
    """Return suburb-level market statistics as supporting evidence.

    Args:
        suburb: Suburb name.
        state: Australian state abbreviation, e.g. NSW.
        postcode: Four-digit postcode.
        property_type: Optional segment, e.g. "apartment".
    """
    return await tools_impl.get_market_context(
        suburb=suburb, state=state, postcode=postcode, property_type=property_type
    )


def main() -> None:
    # stdout carries the JSON-RPC stream; every log line must go to stderr or the
    # transport breaks. This is the single most common way an stdio MCP server fails.
    configure_logging(stream=sys.stderr)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
