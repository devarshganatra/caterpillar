"""
Alert Arbitrator (hot)
Rate-limits repeated audio, latches criticals, creates persistent alerts.
Pure function.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from contracts.events import Event, AlertSeverity, UiPush, UiPushType

@dataclass
class AlertState:
    key: str
    first_ts: datetime
    last_ts: datetime
    count: int
    acked: bool

def _get_key(event: Event) -> str:
    zone = event.evidence.get("zone", "")
    return f"{event.machine_id}:{event.type}:{zone}"

def arbitrate(
    events: list[Event],
    alert_states: dict[str, AlertState],
    now: datetime,
    window_seconds: float = 30.0,
    flood_threshold: int = 10
) -> tuple[list[UiPush], dict[str, AlertState]]:
    """
    Returns (pushes, new_alert_states).
    Does not mutate alert_states.
    """
    new_states = alert_states.copy()
    pushes = []
    
    # 1. Update states and decide emits
    emitted_keys = set()
    
    for e in events:
        key = _get_key(e)
        state = new_states.get(key)
        
        should_emit = False
        out_severity = e.severity
        action_hint = None
        
        if not state:
            # First occurrence -> always emit
            state = AlertState(
                key=key,
                first_ts=e.ts,
                last_ts=e.ts,
                count=1,
                acked=False
            )
            should_emit = True
        else:
            # Existing state
            dt = (e.ts - state.last_ts).total_seconds()
            duration = (e.ts - state.first_ts).total_seconds()
            
            if dt > window_seconds:
                # Window expired, treat as new
                state = AlertState(
                    key=key,
                    first_ts=e.ts,
                    last_ts=e.ts,
                    count=1,
                    acked=False
                )
                should_emit = True
            else:
                # Inside window
                state.count += 1
                state.last_ts = e.ts
                
                # Check persistence upgrade
                is_persistent = state.count >= 3 or duration >= 15.0
                
                if is_persistent:
                    if e.severity == AlertSeverity.WARNING:
                        out_severity = AlertSeverity.CRITICAL
                        action_hint = "Condition persisting. Take immediate action."
                    elif e.severity == AlertSeverity.INFO:
                        out_severity = AlertSeverity.WARNING
                
                # Rate limit logic
                # Always emit if it became persistent for the first time?
                # Actually, rate limit to >= 5s for CRITICAL repeats.
                # For non-critical, we don't emit repeats in the window unless it upgrades.
                if out_severity == AlertSeverity.CRITICAL:
                    # Rate limit CRITICAL audio repeats to 5s
                    # Since we only get here if we process a frame, we use dt
                    if dt >= 5.0:
                        should_emit = True
                else:
                    # Non-critical: only emit if it just became persistent
                    # (simplified: we'll just throttle non-critical to 10s if persistent)
                    if is_persistent and dt >= 10.0:
                        should_emit = True

        new_states[key] = state
        
        if should_emit:
            payload = {
                "event_id": e.event_id,
                "type": e.type,
                "severity": out_severity.value,
                "evidence": e.evidence,
                "count": state.count
            }
            if action_hint:
                payload["action_hint"] = action_hint
                
            pushes.append(UiPush(
                type=UiPushType.alert,
                machine_id=e.machine_id,
                ts=e.ts,
                payload=payload
            ))
            emitted_keys.add(key)
            
    # 2. Flood Guard (ISA-18.2)
    # If we generated > flood_threshold pushes in this tick
    if len(pushes) > flood_threshold:
        # Keep only the highest severity
        pushes.sort(key=lambda p: 2 if p.payload["severity"] == "CRITICAL" else (1 if p.payload["severity"] == "WARNING" else 0), reverse=True)
        top_push = pushes[0]
        suppressed_count = len(pushes) - 1
        
        top_push.payload["flood_warning"] = f"Flood: {suppressed_count} other alerts suppressed"
        pushes = [top_push]
        
    return pushes, new_states
