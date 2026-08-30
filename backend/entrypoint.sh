#!/usr/bin/env bash
# Container entrypoint: wait for PostgreSQL, apply the schema, optionally seed.
#
# Seeding is opt-in via SEED_ON_START so a production deployment never
# accidentally loads demonstration fixtures into a real database.
set -euo pipefail

echo "[entrypoint] waiting for the database…"
python - <<'PY'
import asyncio, sys, time
sys.path.insert(0, "/app")
from app.db.engine import ping

async def wait(timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if await ping():
            print("[entrypoint] database is up")
            return
        await asyncio.sleep(1.5)
    print("[entrypoint] database did not become ready in time", file=sys.stderr)
    sys.exit(1)

asyncio.run(wait())
PY

echo "[entrypoint] applying schema…"
python - <<'PY'
import asyncio, sys
sys.path.insert(0, "/app")
from app.db.schema import create_schema
asyncio.run(create_schema())
print("[entrypoint] schema ready")
PY

if [ "${SEED_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] seeding demonstration data…"
  python scripts/seed_demo_data.py
  echo "[entrypoint] building the semantic index…"
  python scripts/build_embeddings.py
fi

exec "$@"
