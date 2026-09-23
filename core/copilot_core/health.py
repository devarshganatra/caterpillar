"""
Machine Health Rules (hot)
Deterministic thresholds and slopes.
"""
from contracts.events import TelemetryFrame, Event, AlertSeverity
import uuid

def evaluate_health(frame: TelemetryFrame, site_id: str) -> list[Event]:
    events = []
    
    if frame.engine_temp_c > 105:
        events.append(Event(
            event_id=str(uuid.uuid4()),
            type="HEALTH_THRESHOLD",
            severity=AlertSeverity.CRITICAL,
            machine_id=frame.machine_id,
            operator_id=frame.operator_id,
            site_id=site_id,
            ts=frame.ts,
            source_engine="health@1.0",
            evidence={
                "metric": "engine_temp_c",
                "value": frame.engine_temp_c,
                "threshold": 105.0
            }
        ))
        
    if frame.hydraulic_pressure_bar > 320:
        events.append(Event(
            event_id=str(uuid.uuid4()),
            type="HEALTH_THRESHOLD",
            severity=AlertSeverity.WARNING,
            machine_id=frame.machine_id,
            operator_id=frame.operator_id,
            site_id=site_id,
            ts=frame.ts,
            source_engine="health@1.0",
            evidence={
                "metric": "hydraulic_pressure_bar",
                "value": frame.hydraulic_pressure_bar,
                "threshold": 320.0
            }
        ))
        
    return events
