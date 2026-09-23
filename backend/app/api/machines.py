"""
Machine snapshot (for WS reconnect) and window history (Stage 3 Batch 3F).
Every value here comes from Redis/DB — nothing is synthesized when data is
missing; the frontend gets an explicit null/UNAVAILABLE instead.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.db.session import get_db
from backend.app.db.models import Machine, Event as DBEvent, WindowAggregate, IncidentRow, User
from backend.app.api.deps import get_current_user, verify_site_access
from backend.app.services.stream import parse_flat_context
from contracts.machine_config import MACHINES
from core.copilot_core.envelope import calculate_envelope

router = APIRouter()

STALE_THRESHOLD_S = 10.0
IDLE_CAUSES = ["PLANNED", "MACHINE", "WEATHER", "SITE", "OPERATOR"]


async def _get_machine_or_404(machine_id: str, db: AsyncSession) -> Machine:
    result = await db.execute(select(Machine).where(Machine.id == machine_id))
    machine = result.scalar_one_or_none()
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")
    return machine


async def _authorize_machine(machine: Machine, user: User, db: AsyncSession) -> None:
    has_access = await verify_site_access(machine.site_id, user, db)
    if not has_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this site")


def _get_redis() -> Redis:
    from backend.app.services import stream as stream_module
    if stream_module.redis_client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis unavailable")
    return stream_module.redis_client


@router.get("/{machine_id}/snapshot")
async def get_machine_snapshot(
    machine_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    machine = await _get_machine_or_404(machine_id, db)
    await _authorize_machine(machine, user, db)
    redis_client = _get_redis()

    now = datetime.now(timezone.utc)
    snapshot_ts = now.isoformat()

    # --- hot state ---
    raw_state = await redis_client.hgetall(f"machine_state:{machine_id}")

    def _decode(k):
        v = raw_state.get(k.encode())
        return v.decode() if v is not None else None

    hot = None
    context_dict = None
    if raw_state:
        ts_str = _decode("ts")
        hot_ts = datetime.fromisoformat(ts_str) if ts_str else None
        stale = (now - hot_ts).total_seconds() > STALE_THRESHOLD_S if hot_ts else True
        hot = {
            "state": _decode("state"), "ui_mode": _decode("ui_mode"),
            "risk_score": float(_decode("risk_score")) if _decode("risk_score") else None,
            "risk_level": _decode("risk_level"), "last_seq": int(_decode("last_seq")) if _decode("last_seq") else None,
            "ts": ts_str, "stale": stale,
        }

        raw_ctx = await redis_client.hgetall(f"context:{machine.site_id}")
        ctx_frame = parse_flat_context(raw_ctx)
        context_dict = ctx_frame.model_dump(mode="json") if ctx_frame else None
        envelope = calculate_envelope(ctx_frame)
        envelope_dict = {
            "red_radius_m": envelope.red_radius_m, "orange_radius_m": envelope.orange_radius_m,
            "speed_cap_kmh": envelope.speed_cap_kmh, "condition_multiplier": envelope.condition_multiplier,
            "active_conditions": envelope.active_conditions,
        }
    else:
        envelope_dict = None

    # --- recent events (>= WARNING, last 60s) ---
    lookback = now - timedelta(seconds=60)
    events_result = await db.execute(
        select(DBEvent).where(
            DBEvent.machine_id == machine_id, DBEvent.ts >= lookback,
            DBEvent.severity.in_(["WARNING", "CRITICAL"]),
        ).order_by(DBEvent.ts.desc()).limit(10)
    )
    recent_events = [
        {"id": str(e.id), "type": e.type, "severity": e.severity, "ts": e.ts, "evidence": e.evidence}
        for e in events_result.scalars().all()
    ]

    # --- latest window ---
    window_result = await db.execute(
        select(WindowAggregate).where(WindowAggregate.machine_id == machine_id)
        .order_by(WindowAggregate.window_start.desc()).limit(1)
    )
    window_row = window_result.scalar_one_or_none()
    latest_window = None
    task_id_for_eta = None
    if window_row is not None:
        latest_window = {
            "window_id": window_row.window_id, "idle_attribution": window_row.idle_attribution,
            "anomaly": window_row.anomaly,
        }
        task_id_for_eta = window_row.task_id

    # --- ETA (latest LIVE, else BASELINE, for the machine's most recent task) ---
    eta = {"status": "UNAVAILABLE", "unavailable_reason": "NO_TASK"}
    if task_id_for_eta:
        from backend.app.db.models import EtaEstimateRow
        eta_result = await db.execute(
            select(EtaEstimateRow).where(
                EtaEstimateRow.task_id == task_id_for_eta, EtaEstimateRow.kind == "LIVE",
            ).order_by(EtaEstimateRow.ts.desc()).limit(1)
        )
        eta_row = eta_result.scalar_one_or_none()
        if eta_row is None:
            eta_result = await db.execute(
                select(EtaEstimateRow).where(
                    EtaEstimateRow.task_id == task_id_for_eta, EtaEstimateRow.kind == "BASELINE",
                ).limit(1)
            )
            eta_row = eta_result.scalar_one_or_none()
        if eta_row is not None:
            eta = eta_row.payload

    # --- open incidents ---
    incidents_result = await db.execute(
        select(IncidentRow).where(
            IncidentRow.machine_id == machine_id, IncidentRow.status != "CLOSED",
        ).order_by(IncidentRow.last_event_at.desc()).limit(5)
    )
    open_incidents = [
        {
            "id": str(r.id), "machine_id": r.machine_id, "site_id": r.site_id, "operator_id": r.operator_id,
            "task_id": r.task_id, "category": r.category, "severity": r.severity, "escalated": r.escalated,
            "status": r.status, "opened_at": r.opened_at, "last_event_at": r.last_event_at,
            "event_count": r.event_count, "explanation_status": r.explanation_status,
        }
        for r in incidents_result.scalars().all()
    ]

    return {
        "machine_id": machine_id, "site_id": machine.site_id, "snapshot_ts": snapshot_ts,
        "hot": hot, "envelope": envelope_dict, "context": context_dict,
        "recent_events": recent_events, "latest_window": latest_window, "eta": eta,
        "open_incidents": open_incidents,
    }


@router.get("/{machine_id}/windows")
async def get_machine_windows(
    machine_id: str, limit: int = Query(20, le=100),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    machine = await _get_machine_or_404(machine_id, db)
    await _authorize_machine(machine, user, db)

    result = await db.execute(
        select(WindowAggregate).where(WindowAggregate.machine_id == machine_id)
        .order_by(WindowAggregate.window_start.desc()).limit(limit)
    )
    rows = result.scalars().all()

    totals_s = {c: 0.0 for c in IDLE_CAUSES}
    items = []
    for r in rows:
        items.append({
            "window_id": r.window_id, "window_start": r.window_start, "window_end": r.window_end,
            "frame_count": r.frame_count, "idle_attribution": r.idle_attribution, "anomaly": r.anomaly,
            "eta_slip_band": r.last_eta_slip_band,
        })
        if r.idle_attribution and "breakdown_s" in r.idle_attribution:
            for cause, seconds in r.idle_attribution["breakdown_s"].items():
                if cause in totals_s:
                    totals_s[cause] += seconds

    return {"items": items, "totals_s": totals_s}
