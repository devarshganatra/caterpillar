"""
Pure decision logic for event correlation (ARCH section 6.9). No DB/Redis
here — backend.app.worker.correlator applies these decisions inside a
transaction with an advisory lock. Keeping this pure makes the actual
grouping/escalation rules independently unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Optional

SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}

Action = Literal["SKIP_DUPLICATE", "IGNORE_INFO", "OPEN", "EXTEND"]


@dataclass
class IncidentView:
    """Minimal view of an existing open/acknowledged incident, as loaded from the DB."""
    id: str
    severity: str
    status: str
    opened_at: datetime
    last_event_at: datetime
    escalated: bool


@dataclass
class EventView:
    """Minimal view of the Event being correlated (avoids importing contracts.events here)."""
    event_id: str
    type: str
    severity: str
    ts: datetime
    machine_id: str
    evidence: dict | None = None


@dataclass
class Decision:
    action: Action
    escalate: bool = False
    new_severity: Optional[str] = None


def decide(event: EventView, active_incident: Optional[IncidentView], risk_level: str, window_s: int) -> Decision:
    """
    active_incident must already be filtered to status != CLOSED and within
    the correlation window by the caller's DB query (this function does not
    re-derive "active" from a raw incident row — it decides what to do
    GIVEN that an active incident either does or doesn't exist).
    """
    if active_incident is not None:
        old_rank = SEVERITY_RANK.get(active_incident.severity, 0)
        new_rank = SEVERITY_RANK.get(event.severity, 0)
        new_severity = event.severity if new_rank > old_rank else active_incident.severity
        escalate = (new_rank > old_rank) or (risk_level == "HIGH" and not active_incident.escalated)
        return Decision(action="EXTEND", escalate=escalate, new_severity=new_severity)

    if SEVERITY_RANK.get(event.severity, 0) >= SEVERITY_RANK["WARNING"]:
        return Decision(action="OPEN")

    return Decision(action="IGNORE_INFO")


def is_within_correlation_window(event_ts: datetime, incident_opened_at: datetime,
                                  incident_last_event_at: datetime, window_s: int) -> bool:
    """
    An incident is "active" for a new event when the event falls within
    window_s of the incident's last event — with a small backward tolerance
    against opened_at to absorb slight out-of-order delivery within a shard.
    """
    window = timedelta(seconds=window_s)
    return (event_ts - incident_last_event_at) <= window and event_ts >= (incident_opened_at - window)


@dataclass
class TimelineEntryView:
    entry_key: str
    event_type: str
    severity: str
    first_ts: datetime
    last_ts: datetime
    count: int
    representative_event_id: str
    summary: str


def timeline_entry_key(event: EventView) -> str:
    """{type}:{zone|metric|''} — mirrors the arbitrator's alert key shape so
    the same physical condition (e.g. one proximity zone) collapses to one entry."""
    disc = ""
    if event.evidence:
        disc = str(event.evidence.get("zone") or event.evidence.get("metric") or "")
    return f"{event.type}:{disc}"


def merge_into_entry(entry: TimelineEntryView, event: EventView, merge_window_s: float = 30.0) -> Optional[TimelineEntryView]:
    """
    Returns an updated entry if `event` should merge into `entry` (same
    entry_key and within merge_window_s of the entry's last_ts), else None
    (caller should create a new entry instead). Bounds timeline growth —
    hot-path events can fire every frame (~1/s).
    """
    if timeline_entry_key(event) != entry.entry_key:
        return None
    if (event.ts - entry.last_ts).total_seconds() > merge_window_s:
        return None
    return TimelineEntryView(
        entry_key=entry.entry_key, event_type=entry.event_type, severity=entry.severity,
        first_ts=entry.first_ts, last_ts=max(entry.last_ts, event.ts), count=entry.count + 1,
        representative_event_id=entry.representative_event_id, summary=entry.summary,
    )


def new_entry_for_event(event: EventView) -> TimelineEntryView:
    return TimelineEntryView(
        entry_key=timeline_entry_key(event), event_type=event.type, severity=event.severity,
        first_ts=event.ts, last_ts=event.ts, count=1,
        representative_event_id=event.event_id, summary=summarize_event(event),
    )


_SUMMARY_TEMPLATES = {
    "SEATBELT_VIOLATION": lambda e: f"Seatbelt unfastened while {(e.evidence or {}).get('state', 'operating')}",
    "PROXIMITY_BREACH": lambda e: f"Proximity breach in {(e.evidence or {}).get('zone', 'unknown')} zone at {(e.evidence or {}).get('distance_m', '?')}m",
    "OVERSPEED_CONDITION": lambda e: f"Overspeed: {(e.evidence or {}).get('speed_kmh', '?')} km/h (cap {(e.evidence or {}).get('cap_kmh', '?')})",
    "HEALTH_THRESHOLD": lambda e: f"{(e.evidence or {}).get('metric', 'health metric')} threshold breached: {(e.evidence or {}).get('value', '?')}",
    "OPERATIONAL_ANOMALY": lambda e: f"Unusual operating pattern detected (score {(e.evidence or {}).get('score', '?')})",
    "IDLE_DEVIATION": lambda e: f"Idle time above expected for context (×{(e.evidence or {}).get('deviation_ratio', '?')})",
    "ETA_SLIP": lambda e: f"ETA slipping: {(e.evidence or {}).get('slip_pct', '?')} over baseline",
}


def summarize_event(event: EventView) -> str:
    template = _SUMMARY_TEMPLATES.get(event.type)
    if template:
        try:
            return template(event)
        except Exception:
            pass
    return f"{event.type} event ({event.severity})"
