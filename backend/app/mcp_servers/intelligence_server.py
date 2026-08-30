#!/usr/bin/env python3
"""MCP server: internal retrieval and memory capabilities.

Separate from `property-data` on purpose. These tools reach this application's
*own* semantic index and analysis history, not a vendor. Keeping the two servers
apart means an operator can grant an agent read access to internal retrieval
without also granting metered access to a paid property API, and the trust
boundaries stay legible.

Run standalone:
    python -m app.mcp_servers.intelligence_server
"""

from __future__ import annotations

import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.mcp_servers import tools_impl
from app.observability import configure_logging

mcp = FastMCP(
    "property-intelligence",
    instructions=(
        "Semantic retrieval over indexed property listings and market context, plus "
        "lookup of previous analyses. Backed by PostgreSQL and pgvector. Results are "
        "retrieval evidence, never valuations."
    ),
)


@mcp.tool()
async def search_similar_properties(
    query_text: str, limit: int = 10, suburb: str | None = None
) -> dict[str, Any]:
    """Semantically search indexed listing descriptions for qualitatively similar properties.

    Use when the structured filters returned candidates that differ qualitatively
    from the target (condition, aspect, outlook, position).

    Args:
        query_text: Description of the property or qualities being matched.
        limit: Maximum results.
        suburb: Optional suburb restriction.
    """
    return await tools_impl.search_similar_properties(
        query_text=query_text, limit=limit, suburb=suburb
    )


@mcp.tool()
async def retrieve_market_evidence(
    query_text: str, suburb: str | None = None, limit: int = 4
) -> dict[str, Any]:
    """Retrieve suburb market-context evidence relevant to a pricing question.

    Use when a price gap between the asking price and the comparables remains unexplained.

    Args:
        query_text: The question being investigated.
        suburb: Optional suburb restriction.
        limit: Maximum evidence chunks.
    """
    return await tools_impl.retrieve_market_evidence(
        query_text=query_text, suburb=suburb, limit=limit
    )


@mcp.tool()
async def get_previous_analysis(analysis_id: str) -> dict[str, Any]:
    """Retrieve a previously completed analysis and its comparable counts.

    Args:
        analysis_id: UUID of a prior analysis.
    """
    return await tools_impl.get_previous_analysis(analysis_id)


def main() -> None:
    # stdout carries the JSON-RPC stream; every log line must go to stderr or the
    # transport breaks. This is the single most common way an stdio MCP server fails.
    configure_logging(stream=sys.stderr)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
