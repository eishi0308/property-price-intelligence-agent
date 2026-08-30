# syntax=docker/dockerfile:1

# ---- builder -----------------------------------------------------------------
# Dependencies are installed into a venv in a separate stage so the runtime image
# carries no build toolchain and no pip cache.
FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.lock.txt

# ---- runtime -----------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser scripts ./scripts
COPY --chown=appuser:appuser evals ./evals
COPY --chown=appuser:appuser tests ./tests
COPY --chown=appuser:appuser pytest.ini ruff.toml entrypoint.sh ./

RUN chmod +x entrypoint.sh
USER appuser
EXPOSE 8000

# The MCP servers are launched as child processes of this container, so no extra
# service is needed — but they inherit this image's Python and PYTHONPATH.
HEALTHCHECK --interval=20s --timeout=5s --start-period=40s --retries=5 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
