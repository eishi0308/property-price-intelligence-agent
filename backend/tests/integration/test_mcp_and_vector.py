"""MCP tool boundary and pgvector semantic retrieval."""

from __future__ import annotations

import pytest

from app.db import repository
from app.db.engine import session_scope
from app.mcp_servers.client import INPROCESS, STDIO, build_toolset
from app.retrieval.embeddings import get_embeddings
from app.retrieval.hybrid import hybrid_retrieve
from app.retrieval.semantic_text import build_query_document
from app.retrieval.sql_filter import criteria_for, hard_filter
from app.retrieval.vector_store import SOURCE_LISTING, SOURCE_MARKET, similarity_search
from tests.conftest import TARGET_QUERY

pytestmark = pytest.mark.integration

EXPECTED_TOOLS = {
    "resolve_property",
    "get_property_details",
    "get_current_listing",
    "get_historical_listing",
    "search_sold_properties",
    "get_market_context",
    "search_similar_properties",
    "retrieve_market_evidence",
    "get_previous_analysis",
}


@pytest.fixture(scope="session")
async def stdio_toolset():
    toolset = await build_toolset(STDIO)
    yield toolset
    await toolset.aclose()


async def test_mcp_stdio_exposes_every_declared_tool(stdio_toolset):
    if stdio_toolset.transport != STDIO:
        pytest.skip(f"MCP stdio unavailable: {stdio_toolset.degraded_reason}")
    assert set(stdio_toolset.names()) == EXPECTED_TOOLS
    assert set(stdio_toolset.server_names) == {"property-data", "property-intelligence"}


async def test_mcp_tools_carry_json_schemas(stdio_toolset):
    if stdio_toolset.transport != STDIO:
        pytest.skip("MCP stdio unavailable")
    tool = stdio_toolset.get("search_sold_properties")
    schema = (
        tool.args_schema
        if isinstance(tool.args_schema, dict)
        else tool.args_schema.model_json_schema()
    )
    assert "latitude" in schema["properties"]
    assert "radius_km" in schema["properties"]
    assert set(schema.get("required", [])) >= {"latitude", "longitude"}


async def test_mcp_tool_call_round_trip(stdio_toolset, database_ready):
    if stdio_toolset.transport != STDIO:
        pytest.skip("MCP stdio unavailable")
    result = await stdio_toolset.call("resolve_property", query=TARGET_QUERY)
    assert result["ok"] is True
    assert result["property"]["address"].startswith("12/45 Redmyre Road")
    assert result["is_demo_data"] is True


async def test_mcp_errors_cross_the_boundary_as_data_not_exceptions(stdio_toolset):
    if stdio_toolset.transport != STDIO:
        pytest.skip("MCP stdio unavailable")
    result = await stdio_toolset.call("resolve_property", query="not a property")
    assert result["ok"] is False
    assert result["error"]


async def test_inprocess_and_stdio_expose_the_same_tools(stdio_toolset):
    inprocess = await build_toolset(INPROCESS)
    assert inprocess.transport == INPROCESS
    assert set(inprocess.names()) == EXPECTED_TOOLS
    if stdio_toolset.transport == STDIO:
        assert set(inprocess.names()) == set(stdio_toolset.names())


async def test_inprocess_and_stdio_agree_on_results(stdio_toolset, database_ready):
    if stdio_toolset.transport != STDIO:
        pytest.skip("MCP stdio unavailable")
    inprocess = await build_toolset(INPROCESS)
    over_mcp = await stdio_toolset.call("resolve_property", query=TARGET_QUERY)
    direct = await inprocess.call("resolve_property", query=TARGET_QUERY)
    assert over_mcp["property"]["external_id"] == direct["property"]["external_id"]


# --- pgvector -------------------------------------------------------------
async def test_vector_search_returns_ranked_hits(database_ready):
    embedding = await get_embeddings().aembed_query(
        "renovated north-facing apartment with secure parking and a balcony"
    )
    async with session_scope() as session:
        hits = await similarity_search(session, embedding, source_types=[SOURCE_LISTING], k=10)
    assert hits
    similarities = [hit.similarity for hit in hits]
    assert similarities == sorted(
        similarities, reverse=True
    ), "results must be ranked by similarity"
    assert all(-1.0 <= value <= 1.0 for value in similarities)


async def test_vector_search_metadata_filter_restricts_the_candidate_set(
    database_ready, target_property
):
    embedding = await get_embeddings().aembed_query("apartment")
    async with session_scope() as session:
        row = await repository.get_property_by_external_id(
            session, "demo", target_property.external_id
        )
        hits = await similarity_search(
            session, embedding, source_types=[SOURCE_LISTING], property_ids=[row.id], k=10
        )
    assert len(hits) == 1
    assert hits[0].property_id == row.id


async def test_vector_search_respects_source_type_partitioning(database_ready):
    embedding = await get_embeddings().aembed_query("median sale price and volume")
    async with session_scope() as session:
        hits = await similarity_search(session, embedding, source_types=[SOURCE_MARKET], k=5)
    assert hits
    assert all(hit.source_type == SOURCE_MARKET for hit in hits)


async def test_empty_property_filter_short_circuits(database_ready):
    embedding = await get_embeddings().aembed_query("anything")
    async with session_scope() as session:
        assert await similarity_search(session, embedding, property_ids=[], k=5) == []


# --- hybrid fusion --------------------------------------------------------
async def test_hybrid_fusion_combines_three_arms(database_ready, demo_provider, target_property):
    listing = await demo_provider.get_current_listing(target_property.external_id)
    document = build_query_document(target_property, listing)
    embedding = await get_embeddings().aembed_query(document.text)

    async with session_scope() as session:
        row = await repository.get_property_by_external_id(
            session, "demo", target_property.external_id
        )
        filtered = await hard_filter(
            session,
            criteria_for(
                target_property,
                radius_km=3.0,
                lookback_months=12,
                exclude_property_ids=frozenset({row.id}),
            ),
        )
        fused = await hybrid_retrieve(
            session,
            target=target_property,
            target_document=document,
            target_embedding=embedding,
            candidates=filtered.candidates,
            radius_km=3.0,
            limit=20,
        )

    assert fused
    scores = [item.fusion_score for item in fused]
    assert scores == sorted(scores, reverse=True), "fusion output must be ordered"
    assert any(item.vector_rank is not None for item in fused), "semantic arm produced nothing"
    assert any(item.keyword_rank is not None for item in fused), "keyword arm produced nothing"
    assert all(item.structural_rank is not None for item in fused)
    # Provenance for the UI: every arm's contribution is retained.
    top = fused[0]
    assert top.arms_hit()
    assert isinstance(top.shared_concepts, dict)


async def test_hybrid_retrieval_with_no_candidates_returns_empty(database_ready, target_property):
    embedding = await get_embeddings().aembed_query("x")
    document = build_query_document(target_property, None)
    async with session_scope() as session:
        assert (
            await hybrid_retrieve(
                session,
                target=target_property,
                target_document=document,
                target_embedding=embedding,
                candidates=[],
                radius_km=3.0,
            )
            == []
        )
