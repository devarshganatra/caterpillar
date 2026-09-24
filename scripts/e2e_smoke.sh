#!/usr/bin/env bash
# Stage 3 Batch 3J smoke test: brings up the API + all 4 background
# workers + the demo_intel simulator scenario for a few minutes, then
# prints row counts and /admin/workers so a human can eyeball that the
# whole pipeline is alive end to end. Not a pytest suite — that's
# tests/e2e/; this is the "does the real thing actually run" check.
#
# Prereqs: `make up` (Postgres + Redis), `python seed_db.py` already run,
# ml/artifacts present (optional — ETA/anomaly gracefully report
# UNAVAILABLE without it).
#
# Usage: ./scripts/e2e_smoke.sh [duration_seconds]
set -euo pipefail
cd "$(dirname "$0")/.."

DURATION="${1:-180}"
export PYTHONPATH=.
PY=venv/bin/python
LOGDIR=$(mktemp -d)
echo "Logs: $LOGDIR"

pids=()
cleanup() {
  echo
  echo "Stopping (logs kept at $LOGDIR)..."
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT

echo "Starting API..."
"$PY" -m uvicorn backend.app.main:app --port 8000 > "$LOGDIR/api.log" 2>&1 &
pids+=($!)
sleep 2

echo "Starting warm worker..."
"$PY" -m backend.app.worker.warm > "$LOGDIR/warm.log" 2>&1 &
pids+=($!)

echo "Starting correlator..."
"$PY" -m backend.app.worker.correlator > "$LOGDIR/correlator.log" 2>&1 &
pids+=($!)

echo "Starting cold worker..."
"$PY" -m backend.app.worker.cold > "$LOGDIR/cold.log" 2>&1 &
pids+=($!)

sleep 2
echo "Starting simulator (demo_intel scenario, ${DURATION}s)..."
timeout "${DURATION}s" "$PY" simulator/sim.py \
  --scenario simulator/scenarios/demo_intel.yaml --machine EXC001 \
  > "$LOGDIR/sim.log" 2>&1 &
pids+=($!)

echo "Running for ${DURATION}s..."
sleep "$DURATION"

echo
echo "=== /admin/workers ==="
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"demo123"}' | "$PY" -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s http://localhost:8000/admin/workers -H "Authorization: Bearer $TOKEN" | "$PY" -m json.tool

echo
echo "=== Row counts (EXC001) ==="
"$PY" - <<'PYEOF'
import asyncio
from sqlalchemy import select, func
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import WindowAggregate, Event, IncidentRow, IncidentExplanationRow

async def main():
    async with AsyncSessionLocal() as s:
        for label, stmt in [
            ("windows", select(func.count()).select_from(WindowAggregate).where(WindowAggregate.machine_id == "EXC001")),
            ("events", select(func.count()).select_from(Event).where(Event.machine_id == "EXC001")),
            ("incidents", select(func.count()).select_from(IncidentRow).where(IncidentRow.machine_id == "EXC001")),
            ("explanations", select(func.count()).select_from(IncidentExplanationRow).join(
                IncidentRow, IncidentExplanationRow.incident_id == IncidentRow.id
            ).where(IncidentRow.machine_id == "EXC001")),
        ]:
            n = (await s.execute(stmt)).scalar_one()
            print(f"{label}: {n}")

asyncio.run(main())
PYEOF

echo
echo "Smoke run complete. Full logs in $LOGDIR"
