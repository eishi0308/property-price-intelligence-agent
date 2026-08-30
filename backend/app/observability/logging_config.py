"""Structured logging via structlog.

`LOG_FORMAT=json` emits machine-readable lines for a log pipeline; `console`
gives readable local output. This is the always-available observability floor —
it works with zero third-party credentials.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, TextIO

import structlog

_CONFIGURED = False


def configure_logging(
    level: str | None = None, fmt: str | None = None, stream: TextIO | None = None
) -> None:
    """Configure structlog + stdlib logging.

    `stream` matters: an MCP stdio server speaks JSON-RPC on stdout, so a single
    log line written there corrupts the protocol stream. Those servers pass
    `stream=sys.stderr`.
    """
    global _CONFIGURED
    from app.config import get_settings

    settings = get_settings()
    resolved_level = (level or settings.log_level).upper()
    resolved_format = (fmt or settings.log_format).lower()

    target_stream = stream or sys.stdout
    logging.basicConfig(
        format="%(message)s",
        stream=target_stream,
        level=getattr(logging, resolved_level, logging.INFO),
        force=True,
    )
    # Third-party loggers are noisy at DEBUG and drown the agent's own trace.
    for noisy in (
        "httpx",
        "httpcore",
        "urllib3",
        "sqlalchemy.engine.Engine",
        "openai",
        "anthropic",
        "mcp.server.lowlevel.server",
        "mcp.server.streamable_http",
    ):
        logging.getLogger(noisy).setLevel(
            max(logging.WARNING, getattr(logging, resolved_level, 20))
        )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if resolved_format == "json"
        else structlog.dev.ConsoleRenderer(colors=target_stream.isatty())
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, resolved_level, logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger(name)  # type: ignore[return-value]
