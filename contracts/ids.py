"""
Deterministic ID generation for Stage 3 (warm path, correlator, cold worker).

All IDs in this module are UUIDv5 derived from a fixed namespace, so the same
logical input always produces the same ID — across processes, across worker
restarts, and across reprocessing of the same Redis message. This is the
idempotency backbone for the warm worker, correlator and cold worker.

Do NOT use uuid4() anywhere in the warm/correlator/cold path: it breaks
idempotency by construction.
"""
import uuid
from typing import Any

# Fixed namespace UUID for this project. Never change this value — changing
# it would silently change every derived ID and break idempotency against
# anything already persisted.
NS = uuid.UUID("6f1c2d4e-9a3b-4c5d-8e7f-0a1b2c3d4e5f")


def _evidence_key(evidence: dict[str, Any] | None) -> str:
    """
    Extracts the discriminating evidence field used to distinguish multiple
    event types that could fire on the same frame (e.g. two proximity zones).
    Falls back to "" when no discriminating field is present.
    """
    if not evidence:
        return ""
    for key in ("zone", "metric"):
        val = evidence.get(key)
        if val is not None:
            return str(val)
    return ""


def hot_event_id(machine_id: str, frame_seq: int, event_type: str, evidence: dict[str, Any] | None = None) -> str:
    """
    Deterministic event_id for hot-path events (safety/health engines).
    Same (machine_id, frame_seq, type, discriminating evidence) always yields
    the same event_id, so replaying a Redis message never creates a duplicate
    DB row and event references stay stable across restarts.
    """
    disc = _evidence_key(evidence)
    name = f"hot:{machine_id}:{frame_seq}:{event_type}:{disc}"
    return str(uuid.uuid5(NS, name))


def warm_event_id(window_id: str, event_type: str) -> str:
    """Deterministic event_id for warm-path events (anomaly, idle deviation)."""
    name = f"warm:{window_id}:{event_type}"
    return str(uuid.uuid5(NS, name))


def eta_slip_event_id(task_id: str, band: int) -> str:
    """Deterministic event_id for ETA_SLIP events, one per (task, slip band)."""
    name = f"eta_slip:{task_id}:{band}"
    return str(uuid.uuid5(NS, name))


def incident_id(machine_id: str, trigger_event_id: str) -> str:
    """Deterministic incident id, derived from the event that opened it."""
    name = f"incident:{machine_id}:{trigger_event_id}"
    return str(uuid.uuid5(NS, name))


def window_id(machine_id: str, ts_epoch_s: float, window_s: int) -> str:
    """
    Deterministic window id for a machine + event-time window.
    window_start = floor(ts_epoch_s / window_s) * window_s
    """
    window_start = int(ts_epoch_s // window_s) * window_s
    return f"{machine_id}:{window_start}"
