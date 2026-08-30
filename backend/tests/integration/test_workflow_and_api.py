"""The LangGraph workflow end to end, and the FastAPI surface."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.agent.graph import (
    build_graph,
    route_after_evidence_evaluation,
    route_after_evidence_retrieval,
    route_after_resolve,
    run_analysis,
)
from app.main import create_app
from app.schemas.assessment import AssessmentLabel
from tests.conftest import TARGET_QUERY, TARGET_URL

pytestmark = pytest.mark.integration


# --- graph structure ------------------------------------------------------
def test_graph_has_every_node_and_both_loops():
    graph = build_graph().get_graph()
    nodes = set(graph.nodes)
    assert {
        "resolve_property",
        "fetch_target_data",
        "search_sold_candidates",
        "hard_filter",
        "hybrid_retrieval",
        "rerank",
        "evaluate_evidence",
        "expand_search",
        "retrieve_supporting_evidence",
        "research_more",
        "assess_price",
        "guardrail_check",
        "finalise",
        "failure",
    } <= nodes
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("expand_search", "search_sold_candidates") in edges, "outer loop missing"
    assert ("research_more", "retrieve_supporting_evidence") in edges, "inner loop missing"


def test_routing_sends_errors_to_the_failure_node():
    assert route_after_resolve({"error": "boom"}) == "failure"
    assert route_after_resolve({}) == "fetch_target_data"


def test_routing_expands_only_while_budget_and_window_allow():
    weak = {
        "evidence_quality": "insufficient",
        "reranked_comps": [],
        "search_radius_km": 3.0,
        "lookback_months": 12,
        "expansions_used": 0,
    }
    assert route_after_evidence_evaluation(weak) == "expand_search"

    exhausted = {**weak, "expansions_used": 99}
    assert route_after_evidence_evaluation(exhausted) == "retrieve_supporting_evidence"

    at_max = {**weak, "search_radius_km": 999.0, "lookback_months": 999}
    assert (
        route_after_evidence_evaluation(at_max) == "retrieve_supporting_evidence"
    ), "must not burn a retry widening a window that cannot widen"


def test_routing_stops_researching_once_the_retry_budget_is_spent():
    class _Bundle:
        is_sufficient = False

    assert (
        route_after_evidence_retrieval({"evidence": _Bundle(), "evidence_retries": 0})
        == "research_more"
    )
    assert (
        route_after_evidence_retrieval({"evidence": _Bundle(), "evidence_retries": 99})
        == "assess_price"
    )


# --- full runs ------------------------------------------------------------
async def test_full_analysis_produces_a_grounded_assessment(database_ready):
    final, tracer, duration_ms = await run_analysis(analysis_id=str(uuid.uuid4()), query=TARGET_URL)
    assessment = final["assessment"]
    assert assessment is not None
    assert assessment.assessment is not AssessmentLabel.INSUFFICIENT_EVIDENCE
    assert (
        assessment.evidence_range_low
        <= assessment.evidence_median
        <= assessment.evidence_range_high
    )
    assert assessment.supporting_comparables
    assert duration_ms > 0

    # Every cited comparable must exist in the selected set.
    included = {
        item.fused.candidate.external_id for item in final["reranked_comps"] if item.included
    }
    assert {item.external_id for item in assessment.supporting_comparables} <= included

    # The trace must record tool calls and node transitions.
    assert tracer.tool_calls()
    assert any(entry.kind == "node" for entry in tracer.entries)
    assert any(entry.kind == "guardrail" for entry in tracer.entries)


async def test_thin_market_triggers_the_expansion_loop(database_ready):
    final, _tracer, _ms = await run_analysis(
        analysis_id=str(uuid.uuid4()), query="27 Gladstone Street, Concord NSW 2137"
    )
    assert final["expansions_used"] >= 1, "should widen the search on a thin comparable set"
    assert final["search_radius_km"] > 3.0
    searches = [
        entry for entry in final["tool_history"] if entry["tool"] == "search_sold_properties"
    ]
    assert len(searches) >= 2, "widening must actually re-query for new sales"
    assert searches[0]["arguments"]["radius_km"] < searches[-1]["arguments"]["radius_km"]


async def test_unresolvable_query_degrades_gracefully(database_ready):
    final, _tracer, _ms = await run_analysis(analysis_id=str(uuid.uuid4()), query="banana sandwich")
    assessment = final["assessment"]
    assert assessment.assessment is AssessmentLabel.INSUFFICIENT_EVIDENCE
    assert assessment.confidence.value == "low"
    assert assessment.unknowns
    assert final["error"]


async def test_user_exclusions_are_honoured_on_rerun(database_ready):
    first, _tracer, _ms = await run_analysis(analysis_id=str(uuid.uuid4()), query=TARGET_QUERY)
    included = [item for item in first["reranked_comps"] if item.included]
    excluded_id = included[0].fused.candidate.external_id

    second, _tracer2, _ms2 = await run_analysis(
        analysis_id=str(uuid.uuid4()),
        query=TARGET_QUERY,
        user_excluded_external_ids=[excluded_id],
    )
    still_included = {
        item.fused.candidate.external_id for item in second["reranked_comps"] if item.included
    }
    assert excluded_id not in still_included


# --- HTTP API -------------------------------------------------------------
@pytest.fixture(scope="session")
async def client():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        yield async_client


async def test_health_reports_capabilities_and_degradations(client, database_ready):
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] is True
    assert body["pgvector"] is True
    assert body["provider"]["is_demo"] is True
    assert body["mcp"]["tools"]
    assert any("DEMO" in item for item in body["degradations"])
    assert "not a professional property valuation" in body["disclaimer"]


async def test_data_feasibility_endpoint(client, database_ready):
    response = await client.get("/api/data-feasibility")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "demo"
    assert body["vector_retrieval_viable"] in {"YES", "LIMITED", "NO"}
    assert {check["name"] for check in body["checks"]} >= {
        "Target property",
        "Current listing",
        "Sold transactions",
        "Historical descriptions",
    }


async def test_analysis_lifecycle_over_http(client, database_ready):
    created = await client.post("/api/analysis", json={"query": TARGET_QUERY})
    assert created.status_code == 202
    analysis_id = created.json()["id"]

    from app.services import get_analysis_service

    await get_analysis_service().wait_for(uuid.UUID(analysis_id))

    detail = await client.get(f"/api/analysis/{analysis_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "complete"
    assert body["assessment"]["assessment"] in {
        "underpriced",
        "fair",
        "slightly_high",
        "high",
        "insufficient_evidence",
    }
    assert body["metadata"]["mcp_transport"] in {"stdio", "inprocess"}
    assert body["stages"]

    comparables = await client.get(f"/api/analysis/{analysis_id}/comparables")
    assert comparables.status_code == 200
    assert any(item["included"] for item in comparables.json())

    trace = await client.get(f"/api/analysis/{analysis_id}/trace")
    assert trace.status_code == 200
    assert trace.json(), "the local trace must work without LangSmith credentials"


async def test_exclude_then_rerun_recomputes_the_assessment(client, database_ready):
    from app.services import get_analysis_service

    created = await client.post("/api/analysis", json={"query": TARGET_QUERY})
    analysis_id = created.json()["id"]
    await get_analysis_service().wait_for(uuid.UUID(analysis_id))

    before = (await client.get(f"/api/analysis/{analysis_id}")).json()
    included = [item for item in before["comparables"] if item["included"]]
    victim = max(included, key=lambda item: item["sold_price"])

    excluded = await client.post(
        f"/api/analysis/{analysis_id}/comparables/{victim['id']}/exclude",
        json={"reason": "Not comparable in my judgement"},
    )
    assert excluded.status_code == 200
    assert excluded.json()["rerun_required"] is True

    rerun = await client.post(f"/api/analysis/{analysis_id}/rerun")
    assert rerun.status_code == 202
    await get_analysis_service().wait_for(uuid.UUID(analysis_id))

    after = (await client.get(f"/api/analysis/{analysis_id}")).json()
    still_included = {item["id"] for item in after["comparables"] if item["included"]}
    assert victim["id"] not in still_included
    assert any(
        item["excluded_by_user"] and item["id"] == victim["id"] for item in after["comparables"]
    )


async def test_bad_identifiers_and_missing_records_are_handled(client):
    assert (await client.get("/api/analysis/not-a-uuid")).status_code == 400
    assert (await client.get(f"/api/analysis/{uuid.uuid4()}")).status_code == 404
    assert (await client.post(f"/api/analysis/{uuid.uuid4()}/rerun")).status_code == 404


async def test_request_validation_rejects_bad_payloads(client):
    assert (await client.post("/api/analysis", json={"query": "ab"})).status_code == 422
    assert (await client.post("/api/analysis", json={})).status_code == 422
    assert (
        await client.post("/api/analysis", json={"query": TARGET_QUERY, "unexpected": 1})
    ).status_code == 422
