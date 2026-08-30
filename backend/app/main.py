"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import router
from app.config import get_settings
from app.db.engine import dispose_engine, pgvector_available, ping
from app.guardrails import DISCLAIMER
from app.mcp_servers import get_toolset, shutdown_toolset
from app.observability import configure_logging, configure_tracing, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging()
    configure_tracing()

    database_up = await ping()
    vector_up = await pgvector_available() if database_up else False
    if not database_up:
        # Started anyway: /api/health must stay reachable to report *why* the
        # service is unusable. Failing to boot would hide the diagnosis.
        logger.error(
            "startup.database_unreachable", database_url_host=settings.database_url.split("@")[-1]
        )
    elif not vector_up:
        logger.error("startup.pgvector_missing", hint="run: CREATE EXTENSION vector;")

    # Warm the MCP session so the first analysis does not pay subprocess startup.
    toolset = await get_toolset()
    logger.info(
        "startup.complete",
        provider=settings.property_provider.value,
        llm_backend=settings.resolved_llm_backend.value,
        embedding_backend=settings.resolved_embedding_backend.value,
        mcp_transport=toolset.transport,
        mcp_tools=len(toolset.names()),
        database=database_up,
        pgvector=vector_up,
    )
    try:
        yield
    finally:
        await shutdown_toolset()
        await dispose_engine()
        logger.info("shutdown.complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Property Price Intelligence Agent",
        version="0.1.0",
        description=(
            "Buyer-side evidence intelligence for Australian residential property.\n\n"
            f"{DISCLAIMER}"
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("api.unhandled", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "An unexpected error occurred.",
                "error_type": type(exc).__name__,
                "path": request.url.path,
            },
        )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": "Property Price Intelligence Agent",
            "docs": "/docs",
            "health": "/api/health",
            "disclaimer": DISCLAIMER,
        }

    return app


app = create_app()
