"""MCP servers and client.

Responsibility split (kept strictly, because conflating these is the most common
way these stacks turn into mush):

    MCP        the capability boundary — what the agent may reach for, described
               as typed tools with schemas, independent of this process.
    LangGraph  workflow orchestration — what happens, in what order, and what to
               do when the evidence is not good enough.
    LangChain  LLM, retriever and structured-output components.
    RAG        evidence retrieval and grounding.
    pgvector   the semantic retrieval substrate.
"""

from app.mcp_servers.client import (  # noqa: F401
    MCPToolset,
    get_toolset,
    shutdown_toolset,
)
