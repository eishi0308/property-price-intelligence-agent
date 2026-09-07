"""MCP client — how the agent reaches its tools.

TWO TRANSPORTS, ONE TOOL CONTRACT
---------------------------------
``stdio`` (default)
    Launches the two MCP servers as subprocesses and loads their tools through
    `langchain_mcp_adapters`, so the agent talks to them over a real MCP session
    with real JSON-schema-typed tools. This is the honest MCP boundary: the
    servers could equally be running elsewhere, written in another language, or
    consumed by a different client.

``inprocess``
    Binds the same implementations as LangChain `StructuredTool`s in this
    process. Used by tests and by deployments where a subprocess per request is
    not acceptable. It is not a fake — it runs the identical functions — but it
    is *not* MCP, and `MCPToolset.transport` says so plainly wherever it matters.

The toolset is created once and reused; establishing an MCP session per analysis
would dominate latency.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.mcp_servers import tools_impl
from app.observability import get_logger

logger = get_logger(__name__)

STDIO = "stdio"
INPROCESS = "inprocess"

PROPERTY_DATA_SERVER = "property-data"
INTELLIGENCE_SERVER = "property-intelligence"

#: name -> (server, implementation)
_TOOL_REGISTRY: dict[str, tuple[str, Any]] = {
    "resolve_property": (PROPERTY_DATA_SERVER, tools_impl.resolve_property),
    "get_property_details": (PROPERTY_DATA_SERVER, tools_impl.get_property_details),
    "get_current_listing": (PROPERTY_DATA_SERVER, tools_impl.get_current_listing),
    "get_historical_listing": (PROPERTY_DATA_SERVER, tools_impl.get_historical_listing),
    "search_sold_properties": (PROPERTY_DATA_SERVER, tools_impl.search_sold_properties),
    "get_market_context": (PROPERTY_DATA_SERVER, tools_impl.get_market_context),
    "search_similar_properties": (INTELLIGENCE_SERVER, tools_impl.search_similar_properties),
    "retrieve_market_evidence": (INTELLIGENCE_SERVER, tools_impl.retrieve_market_evidence),
    "get_previous_analysis": (INTELLIGENCE_SERVER, tools_impl.get_previous_analysis),
}


@dataclass
class MCPToolset:
    """The agent's tool surface, however it was obtained."""

    tools: dict[str, BaseTool] = field(default_factory=dict)
    transport: str = INPROCESS
    server_names: list[str] = field(default_factory=list)
    degraded_reason: str | None = None
    _sessions: _PersistentSessions | None = None

    def get(self, name: str) -> BaseTool:
        tool = self.tools.get(name)
        if tool is None:
            raise KeyError(
                f"Tool '{name}' is not available. Loaded: {sorted(self.tools)} "
                f"(transport={self.transport})."
            )
        return tool

    def names(self) -> list[str]:
        return sorted(self.tools)

    async def call(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """Invoke a tool by name and normalise its result to a dict."""
        result = await self.get(name).ainvoke(kwargs)
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            import json

            try:
                parsed = json.loads(result)
                return parsed if isinstance(parsed, dict) else {"ok": True, "result": parsed}
            except json.JSONDecodeError:
                return {"ok": True, "result": result}
        return {"ok": True, "result": result}

    async def aclose(self) -> None:
        if self._sessions is not None:
            await self._sessions.stop()
            self._sessions = None


def _build_inprocess_tools() -> dict[str, BaseTool]:
    tools: dict[str, BaseTool] = {}
    for name, (_server, implementation) in _TOOL_REGISTRY.items():
        tools[name] = StructuredTool.from_function(
            coroutine=implementation,
            name=name,
            description=(implementation.__doc__ or name).strip().split("\n\n")[0],
        )
    return tools


def _server_connections() -> dict[str, dict[str, Any]]:
    backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, "PYTHONPATH": backend_root}
    return {
        PROPERTY_DATA_SERVER: {
            "command": sys.executable,
            "args": ["-m", "app.mcp_servers.property_data_server"],
            "transport": "stdio",
            "cwd": backend_root,
            "env": env,
        },
        INTELLIGENCE_SERVER: {
            "command": sys.executable,
            "args": ["-m", "app.mcp_servers.intelligence_server"],
            "transport": "stdio",
            "cwd": backend_root,
            "env": env,
        },
    }


class _PersistentSessions:
    """Owns long-lived MCP sessions from a single dedicated task.

    Why this exists: creating tools via `MultiServerMCPClient.get_tools()` opens a
    fresh stdio session — and therefore spawns a fresh Python subprocess — on every
    single tool call. Measured here, that was ~1.5 s of pure overhead per call, which
    dominated the entire analysis.

    Holding the sessions open is not as simple as stashing an `AsyncExitStack` on a
    module global: anyio cancel scopes must be entered and exited by the *same* task,
    and this toolset is shared across many request tasks. So one owner task enters the
    stack, publishes the loaded tools, and then parks until shutdown. Other tasks only
    ever send and receive on the session's memory streams, which is safe across tasks.
    """

    def __init__(self, connections: dict[str, dict[str, Any]]) -> None:
        self._connections = connections
        self._tools: dict[str, BaseTool] = {}
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None

    async def _run(self) -> None:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.tools import load_mcp_tools

        try:
            client = MultiServerMCPClient(self._connections)  # type: ignore[arg-type]
            async with AsyncExitStack() as stack:
                loaded: dict[str, BaseTool] = {}
                for server_name in self._connections:
                    session = await stack.enter_async_context(client.session(server_name))
                    for tool in await load_mcp_tools(session, server_name=server_name):
                        loaded[tool.name] = tool
                if not loaded:
                    raise RuntimeError("MCP servers exposed no tools.")
                self._tools = loaded
                self._ready.set()
                await self._stop.wait()
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            raise

    async def start(self, timeout_seconds: float) -> dict[str, BaseTool]:
        self._task = asyncio.create_task(self._run(), name="mcp-session-owner")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout_seconds)
        except TimeoutError as exc:
            await self.stop()
            raise RuntimeError(
                f"MCP servers did not become ready within {timeout_seconds:g}s."
            ) from exc
        if self._error is not None:
            raise RuntimeError(f"{type(self._error).__name__}: {self._error}") from self._error
        return self._tools

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            # Shutdown is best-effort: the subprocesses are going away regardless, and a
            # teardown error must not mask the real reason we are stopping.
            with suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


async def build_toolset(transport: str = STDIO, timeout_seconds: float = 60.0) -> MCPToolset:
    """Create the toolset, falling back to in-process if MCP cannot start.

    A fallback is recorded in `degraded_reason` and surfaced through
    `/api/health`, so a silently-degraded MCP boundary cannot go unnoticed.
    """
    if transport == INPROCESS:
        return MCPToolset(
            tools=_build_inprocess_tools(),
            transport=INPROCESS,
            server_names=sorted({server for server, _ in _TOOL_REGISTRY.values()}),
        )

    sessions = _PersistentSessions(_server_connections())
    try:
        tools = await sessions.start(timeout_seconds)
        logger.info("mcp.connected", transport=STDIO, tools=sorted(tools), count=len(tools))
        return MCPToolset(
            tools=tools,
            transport=STDIO,
            server_names=list(_server_connections()),
            _sessions=sessions,
        )
    except Exception as exc:
        await sessions.stop()
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning("mcp.stdio_unavailable", error=reason, falling_back_to=INPROCESS)
        return MCPToolset(
            tools=_build_inprocess_tools(),
            transport=INPROCESS,
            server_names=sorted({server for server, _ in _TOOL_REGISTRY.values()}),
            degraded_reason=(
                f"MCP stdio transport unavailable ({reason}); tools are bound in-process "
                f"instead. Behaviour is identical, but the MCP boundary is not exercised."
            ),
        )


_TOOLSET: MCPToolset | None = None
_LOCK = asyncio.Lock()


async def get_toolset(transport: str | None = None) -> MCPToolset:
    global _TOOLSET
    async with _LOCK:
        if _TOOLSET is None:
            resolved: str = transport or os.environ.get("MCP_TRANSPORT") or STDIO
            _TOOLSET = await build_toolset(resolved)
        return _TOOLSET


async def shutdown_toolset() -> None:
    global _TOOLSET
    async with _LOCK:
        if _TOOLSET is not None:
            await _TOOLSET.aclose()
            _TOOLSET = None
