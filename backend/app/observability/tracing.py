"""Run tracing.

Two layers, by design:

* **LangSmith** — rich, hosted, opt-in. Enabled only when both
  `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_API_KEY` are present.
* **`TraceRecorder`** — always on. Records every node transition, tool call,
  retrieval and LLM call to `agent_traces`, which is what
  `GET /api/analysis/{id}/trace` serves.

The local recorder is not a fallback nicety: without it, the "show your working"
guarantee of this product would depend on a third-party subscription.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.observability.logging_config import get_logger

logger = get_logger(__name__)


def configure_tracing() -> dict[str, Any]:
    """Wire LangSmith environment variables if — and only if — credentials exist."""
    settings = get_settings()
    if settings.tracing_enabled:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key or ""
        os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
        logger.info("tracing.enabled", backend="langsmith", project=settings.langchain_project)
        return {"enabled": True, "backend": "langsmith", "project": settings.langchain_project}

    # Explicitly off, so a stray env var cannot half-enable tracing and stall calls.
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    reason = (
        "LANGCHAIN_API_KEY not set"
        if settings.langchain_tracing_v2
        else "LANGCHAIN_TRACING_V2 not enabled"
    )
    logger.info("tracing.local_only", reason=reason)
    return {"enabled": False, "backend": "local", "reason": reason}


def tracing_status() -> dict[str, Any]:
    settings = get_settings()
    return {
        "langsmith_enabled": settings.tracing_enabled,
        "project": settings.langchain_project if settings.tracing_enabled else None,
        "local_trace_table": "agent_traces",
    }


@dataclass
class TraceEntry:
    sequence: int
    kind: str
    name: str
    status: str = "ok"
    duration_ms: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceRecorder:
    """Collects an ordered trace for one analysis run."""

    entries: list[TraceEntry] = field(default_factory=list)
    _sequence: int = 0

    def record(
        self,
        kind: str,
        name: str,
        *,
        status: str = "ok",
        duration_ms: int | None = None,
        **detail: Any,
    ) -> TraceEntry:
        self._sequence += 1
        entry = TraceEntry(
            sequence=self._sequence,
            kind=kind,
            name=name,
            status=status,
            duration_ms=duration_ms,
            detail={key: value for key, value in detail.items() if value is not None},
        )
        self.entries.append(entry)
        logger.info(
            f"trace.{kind}", name=name, status=status, duration_ms=duration_ms, **entry.detail
        )
        return entry

    def span(self, kind: str, name: str, **detail: Any) -> _Span:
        return _Span(self, kind, name, detail)

    def tool_calls(self) -> list[TraceEntry]:
        return [entry for entry in self.entries if entry.kind == "tool_call"]

    def to_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "sequence": entry.sequence,
                "kind": entry.kind,
                "name": entry.name,
                "status": entry.status,
                "duration_ms": entry.duration_ms,
                "detail": entry.detail,
            }
            for entry in self.entries
        ]


class _Span:
    """Context manager that times a block and records success or failure."""

    def __init__(
        self, recorder: TraceRecorder, kind: str, name: str, detail: dict[str, Any]
    ) -> None:
        self._recorder = recorder
        self._kind = kind
        self._name = name
        self._detail = detail
        self._started = 0.0

    def __enter__(self) -> _Span:
        self._started = time.perf_counter()
        return self

    def annotate(self, **detail: Any) -> None:
        self._detail.update(detail)

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any
    ) -> bool:
        duration_ms = int((time.perf_counter() - self._started) * 1000)
        if exc_type is not None:
            self._recorder.record(
                self._kind,
                self._name,
                status="error",
                duration_ms=duration_ms,
                error=f"{exc_type.__name__}: {exc}",
                **self._detail,
            )
        else:
            self._recorder.record(self._kind, self._name, duration_ms=duration_ms, **self._detail)
        return False
