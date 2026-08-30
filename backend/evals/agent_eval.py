"""Agent evaluation: does the workflow behave correctly, not just produce output?

Scenario-driven. Each scenario states what the agent *should* do, and the result
is checked against the recorded trace rather than the final answer, because an
agent can reach a plausible answer through incorrect behaviour — and that is
exactly the failure mode that bites later.

    tool_choice          were the required tools called (and no forbidden ones)
    workflow_completion  did the run reach a terminal state with an assessment
    search_expansion     did it widen the search exactly when it should have
    unnecessary_calls    duplicate tool calls with identical arguments
    failure_recovery     does an unresolvable input degrade gracefully
    latency / cost       wall-clock, tool-call count, LLM-call count
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent.graph import run_analysis
from app.observability import TraceRecorder, get_logger

logger = get_logger(__name__)


@dataclass
class Scenario:
    name: str
    query: str
    description: str
    expect_success: bool = True
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    expect_expansion: bool | None = None  # None = don't care
    expect_assessment_in: tuple[str, ...] = ()


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="resolve_from_rea_url",
        query="https://www.realestate.com.au/property-apartment-nsw-strathfield-145820394",
        description="A pasted realestate.com.au URL resolves and completes without widening.",
        required_tools=("resolve_property", "get_current_listing", "search_sold_properties"),
        expect_expansion=False,
        expect_assessment_in=("fair", "slightly_high", "high", "underpriced"),
    ),
    Scenario(
        name="resolve_from_address",
        query="5/8 Everton Road, Burwood NSW 2134",
        description="A plain street address resolves and completes.",
        required_tools=("resolve_property", "search_sold_properties"),
        expect_assessment_in=("fair", "slightly_high", "high", "underpriced"),
    ),
    Scenario(
        name="thin_market_expansion",
        query="27 Gladstone Street, Concord NSW 2137",
        description=(
            "A house in a thin segment has too few comparables at the default radius; "
            "the agent must widen the search rather than answer on weak evidence."
        ),
        required_tools=("resolve_property", "search_sold_properties"),
        expect_expansion=True,
    ),
    Scenario(
        name="unresolvable_input",
        query="not a property at all",
        description="Unresolvable input must fail gracefully with insufficient_evidence.",
        expect_success=False,
        required_tools=("resolve_property",),
        forbidden_tools=("search_sold_properties",),
        expect_assessment_in=("insufficient_evidence",),
    ),
    Scenario(
        name="unsupported_site_url",
        query="https://www.zillow.com/homedetails/12345",
        description="An unsupported property site must be rejected before any data call.",
        expect_success=False,
        required_tools=("resolve_property",),
        forbidden_tools=("search_sold_properties", "get_current_listing"),
        expect_assessment_in=("insufficient_evidence",),
    ),
)


@dataclass
class ScenarioResult:
    scenario: Scenario
    passed: bool = False
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    duration_ms: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    duplicate_calls: int = 0
    expansions: int = 0
    assessment: str | None = None
    tools_used: list[str] = field(default_factory=list)


@dataclass
class AgentEvalResult:
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return (
            sum(1 for item in self.results if item.passed) / len(self.results)
            if self.results
            else 0.0
        )

    @property
    def mean_latency_ms(self) -> float:
        return (
            sum(item.duration_ms for item in self.results) / len(self.results)
            if self.results
            else 0.0
        )

    @property
    def total_tool_calls(self) -> int:
        return sum(item.tool_calls for item in self.results)

    @property
    def total_llm_calls(self) -> int:
        return sum(item.llm_calls for item in self.results)


def _duplicate_tool_calls(history: list[dict[str, Any]]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for entry in history:
        signature = (
            f"{entry.get('tool')}::{sorted((entry.get('arguments') or {}).items(), key=str)}"
        )
        if signature in seen:
            duplicates += 1
        seen.add(signature)
    return duplicates


def _evaluate(
    scenario: Scenario, final: dict[str, Any], tracer: TraceRecorder, duration_ms: int
) -> ScenarioResult:
    history = final.get("tool_history") or []
    tools_used = [entry["tool"] for entry in history]
    assessment = final.get("assessment")
    label = assessment.assessment.value if assessment else None

    result = ScenarioResult(
        scenario=scenario,
        duration_ms=duration_ms,
        tool_calls=len(history),
        llm_calls=sum(
            1 for entry in tracer.entries if entry.kind == "llm" and entry.status != "skipped"
        ),
        duplicate_calls=_duplicate_tool_calls(history),
        expansions=final.get("expansions_used", 0),
        assessment=label,
        tools_used=tools_used,
    )

    missing = [tool for tool in scenario.required_tools if tool not in tools_used]
    result.checks["required_tools_called"] = not missing
    if missing:
        result.failures.append(f"required tools not called: {', '.join(missing)}")

    forbidden = [tool for tool in scenario.forbidden_tools if tool in tools_used]
    result.checks["no_forbidden_tools"] = not forbidden
    if forbidden:
        result.failures.append(f"forbidden tools called: {', '.join(forbidden)}")

    completed = assessment is not None
    result.checks["workflow_completed"] = completed
    if not completed:
        result.failures.append("no assessment was produced")

    if scenario.expect_assessment_in:
        ok = label in scenario.expect_assessment_in
        result.checks["assessment_as_expected"] = ok
        if not ok:
            result.failures.append(
                f"assessment '{label}' not in expected {scenario.expect_assessment_in}"
            )

    if scenario.expect_expansion is not None:
        expanded = result.expansions > 0
        ok = expanded == scenario.expect_expansion
        result.checks["search_expansion_correct"] = ok
        if not ok:
            result.failures.append(
                f"expected expansion={scenario.expect_expansion}, observed {result.expansions}"
            )

    result.checks["no_duplicate_tool_calls"] = result.duplicate_calls == 0
    if result.duplicate_calls:
        # Not fatal — an expansion loop legitimately repeats the sold-sales search
        # with *different* arguments; identical repeats are the waste we care about.
        result.failures.append(f"{result.duplicate_calls} duplicate tool call(s)")

    if not scenario.expect_success:
        graceful = completed and label == "insufficient_evidence"
        result.checks["failed_gracefully"] = graceful
        if not graceful:
            result.failures.append("did not degrade gracefully to insufficient_evidence")

    result.passed = all(result.checks.values())
    return result


async def evaluate_agent(scenarios: tuple[Scenario, ...] = SCENARIOS) -> AgentEvalResult:
    outcome = AgentEvalResult()
    for index, scenario in enumerate(scenarios):
        try:
            final, tracer, duration_ms = await run_analysis(
                analysis_id=f"eval-agent-{index}", query=scenario.query
            )
            outcome.results.append(_evaluate(scenario, final, tracer, duration_ms))
        except Exception as exc:
            logger.warning("eval.agent_scenario_crashed", scenario=scenario.name, error=str(exc))
            crashed = ScenarioResult(scenario=scenario, passed=False)
            crashed.failures.append(f"crashed: {type(exc).__name__}: {exc}")
            crashed.checks["no_crash"] = False
            outcome.results.append(crashed)
    return outcome


def render_agent(result: AgentEvalResult) -> str:
    lines = ["", "AGENT EVALUATION", "=" * 78, ""]
    lines.append(
        f"Pass rate {result.pass_rate:.0%}  |  mean latency {result.mean_latency_ms:.0f} ms  |  "
        f"{result.total_tool_calls} tool calls  |  {result.total_llm_calls} LLM calls"
    )
    lines.append("")
    for item in result.results:
        status = "PASS" if item.passed else "FAIL"
        lines.append(f"  [{status}] {item.scenario.name}")
        lines.append(f"         {item.scenario.description}")
        lines.append(
            f"         {item.duration_ms} ms | tools={item.tool_calls} "
            f"({', '.join(item.tools_used) or 'none'}) | expansions={item.expansions} "
            f"| assessment={item.assessment}"
        )
        for name, ok in item.checks.items():
            lines.append(f"           {'ok ' if ok else 'X  '} {name}")
        for failure in item.failures:
            lines.append(f"           -> {failure}")
        lines.append("")
    lines.append("COST NOTE")
    lines.append(
        "  LLM call count is the cost driver: it is the number of reranker grades plus one "
        "narration per analysis. With no LLM configured it is zero and the deterministic "
        "components run instead."
    )
    return "\n".join(lines)
