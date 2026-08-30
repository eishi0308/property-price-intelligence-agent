#!/usr/bin/env python3
"""Run the full evaluation suite.

python -m evals.run                # everything
python -m evals.run --only retrieval
python -m evals.run --json report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.engine import dispose_engine, pgvector_available, ping
from app.llm import llm_backend_name
from app.mcp_servers import get_toolset, shutdown_toolset
from app.observability import configure_logging
from app.retrieval.embeddings import embedding_model_name
from evals.agent_eval import evaluate_agent, render_agent
from evals.rag_eval import evaluate_rag, render_rag
from evals.retrieval_eval import evaluate_retrieval, render_retrieval

RAG_QUERIES = [
    "12/45 Redmyre Road, Strathfield NSW 2135",
    "5/8 Everton Road, Burwood NSW 2134",
    "27 Gladstone Street, Concord NSW 2137",
]


def _header() -> str:
    settings = get_settings()
    lines = [
        "",
        "=" * 78,
        "PROPERTY PRICE INTELLIGENCE AGENT — EVALUATION REPORT",
        "=" * 78,
        f"Generated      : {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC",
        f"Provider       : {settings.property_provider.value}",
        f"LLM backend    : {llm_backend_name(settings)}",
        f"Embedding model: {embedding_model_name(settings)}",
        "",
    ]
    if settings.is_fully_offline:
        lines.append(
            "RUNNING FULLY OFFLINE. Numbers below measure the deterministic fallback\n"
            "components on synthetic fixtures. They establish that the pipeline works and\n"
            "that each stage is measurable — they are NOT evidence of production retrieval\n"
            "quality with a real embedding model or a live property data provider."
        )
        lines.append("")
    return "\n".join(lines)


def _serialise(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _serialise(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _serialise(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_serialise(item) for item in value]
    return value


async def main_async(args: argparse.Namespace) -> int:
    configure_logging(level="WARNING")
    if not await ping():
        print("FATAL: PostgreSQL is unreachable. Start it and run scripts/seed_demo_data.py.")
        return 2
    if not await pgvector_available():
        print("FATAL: the pgvector extension is not installed in this database.")
        return 2
    await get_toolset()

    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat()}
    print(_header())

    selected = args.only or ["retrieval", "rag", "agent"]
    exit_code = 0

    if "retrieval" in selected:
        retrieval = await evaluate_retrieval()
        print(render_retrieval(retrieval))
        report["retrieval"] = _serialise(retrieval.by_configuration)
        baseline = retrieval.by_configuration.get("structural_only", {}).get("nDCG@10", 0)
        hybrid = retrieval.by_configuration.get("hybrid_rrf", {}).get("nDCG@10", 0)
        report["retrieval_hybrid_beats_baseline"] = bool(hybrid > baseline)

    if "rag" in selected:
        rag = await evaluate_rag(RAG_QUERIES)
        print(render_rag(rag))
        report["rag"] = {
            "retrieval_relevance": round(rag.average("retrieval_relevance"), 3),
            "groundedness": round(rag.average("groundedness"), 3),
            "citation_correctness": round(rag.average("citation_correctness"), 3),
            "unsupported_claim_rate": round(rag.unsupported_claim_rate, 3),
        }
        # A grounding failure is a correctness failure, not a warning.
        if rag.unsupported_claim_rate > 0:
            exit_code = max(exit_code, 1)

    if "agent" in selected:
        agent = await evaluate_agent()
        print(render_agent(agent))
        report["agent"] = {
            "pass_rate": round(agent.pass_rate, 3),
            "mean_latency_ms": round(agent.mean_latency_ms, 1),
            "tool_calls": agent.total_tool_calls,
            "llm_calls": agent.total_llm_calls,
            "scenarios": [
                {
                    "name": item.scenario.name,
                    "passed": item.passed,
                    "checks": item.checks,
                    "failures": item.failures,
                    "duration_ms": item.duration_ms,
                    "assessment": item.assessment,
                }
                for item in agent.results
            ],
        }
        if agent.pass_rate < 1.0:
            exit_code = max(exit_code, 1)

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for section, payload in report.items():
        if isinstance(payload, dict) and section in {"rag", "agent"}:
            for key, value in payload.items():
                if not isinstance(value, list | dict):
                    print(f"  {section}.{key:<28} {value}")
        elif section == "retrieval":
            for configuration, row in payload.items():
                print(
                    f"  retrieval.{configuration:<22} nDCG@10={row.get('nDCG@10')}  P@5={row.get('P@5')}"
                )
    print(f"\n  exit_code                        {exit_code}  (non-zero = a quality gate failed)")
    print("")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"  JSON report written to {args.json}\n")

    await shutdown_toolset()
    await dispose_engine()
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the evaluation suite.")
    parser.add_argument(
        "--only",
        nargs="*",
        choices=["retrieval", "rag", "agent"],
        help="Run only the named suites.",
    )
    parser.add_argument("--json", help="Also write a machine-readable report to this path.")
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
