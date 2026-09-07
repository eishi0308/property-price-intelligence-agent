"""Architectural constraint test for the MCP tool dispatcher.

`_call_tool(state, name, /, **kwargs)` forwards `**kwargs` straight to an MCP
tool. Several of those tools take their own `state` argument — the Australian
state — and `get_market_context` requires it. If the dispatcher's own leading
parameters are ever made keyword-assignable again, `state="NSW"` binds to the
dispatcher instead of being forwarded and the call dies with "multiple values
for argument 'state'" before the tool is ever reached.

That is invisible to the rest of the suite, because the branch that calls
`get_market_context` only runs when the evidence bundle is missing market
context, which the demo corpus always supplies. So the invariant is pinned
here rather than left to be rediscovered in production.
"""

from __future__ import annotations

import inspect

import pytest

from app.agent.nodes import _call_tool

pytestmark = pytest.mark.unit

DISPATCH_PARAMS = ("state", "name")


def test_dispatch_parameters_are_positional_only() -> None:
    parameters = inspect.signature(_call_tool).parameters
    for param in DISPATCH_PARAMS:
        assert parameters[param].kind is inspect.Parameter.POSITIONAL_ONLY, (
            f"{param!r} must stay positional-only, or a tool argument of the same "
            f"name can no longer be forwarded"
        )


def test_tool_kwargs_named_like_dispatch_parameters_are_forwarded() -> None:
    bound = inspect.signature(_call_tool).bind(
        {"query": "5/8 Everton Road, Burwood"},
        "get_market_context",
        suburb="Burwood",
        state="NSW",
        postcode="2134",
        property_type="house",
    )

    assert bound.arguments["name"] == "get_market_context"
    assert bound.arguments["kwargs"]["state"] == "NSW"
