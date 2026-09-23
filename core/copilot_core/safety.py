"""
Safety Evaluator (hot)
Evaluates seatbelt, proximity, overspeed against envelope.
"""
from contracts.events import TelemetryFrame, Event, AlertSeverity, MachineState
from core.copilot_core.envelope import SafetyEnvelope
import uuid

def evaluate_safety(
    frame: TelemetryFrame,
    state: MachineState,
    envelope: SafetyEnvelope,
    idle_ticks: int,
    site_id: str
) -> list[Event]:
    events = []
    
    # 1. Seatbelt rules
    if frame.seatbelt == "UNFASTENED":
        if state in (MachineState.TRAVEL, MachineState.WORKING):
            events.append(Event(
                event_id=str(uuid.uuid4()),
                type="SEATBELT_VIOLATION",
                severity=AlertSeverity.CRITICAL,
                machine_id=frame.machine_id,
                operator_id=frame.operator_id,
                site_id=site_id,
                ts=frame.ts,
                source_engine="safety@1.0",
                evidence={"state": state.value}
            ))
        elif state == MachineState.IDLE and idle_ticks > 10:
            events.append(Event(
                event_id=str(uuid.uuid4()),
                type="SEATBELT_VIOLATION",
                severity=AlertSeverity.WARNING,
                machine_id=frame.machine_id,
                operator_id=frame.operator_id,
                site_id=site_id,
                ts=frame.ts,
                source_engine="safety@1.0",
                evidence={"state": state.value, "idle_ticks": idle_ticks}
            ))

    # 2. Proximity rules
    if frame.proximity:
        dist = frame.proximity.distance_m
        
        # Check against dynamic envelope
        if dist < envelope.red_radius_m or frame.proximity.zone == "RED":
            events.append(Event(
                event_id=str(uuid.uuid4()),
                type="PROXIMITY_BREACH",
                severity=AlertSeverity.CRITICAL,
                machine_id=frame.machine_id,
                operator_id=frame.operator_id,
                site_id=site_id,
                ts=frame.ts,
                source_engine="safety@1.0",
                evidence={
                    "zone": "RED",
                    "distance_m": dist,
                    "effective_radius_m": envelope.red_radius_m,
                    "multiplier": envelope.condition_multiplier
                }
            ))
        elif dist < envelope.orange_radius_m or frame.proximity.zone == "ORANGE":
            events.append(Event(
                event_id=str(uuid.uuid4()),
                type="PROXIMITY_BREACH",
                severity=AlertSeverity.WARNING,
                machine_id=frame.machine_id,
                operator_id=frame.operator_id,
                site_id=site_id,
                ts=frame.ts,
                source_engine="safety@1.0",
                evidence={
                    "zone": "ORANGE",
                    "distance_m": dist,
                    "effective_radius_m": envelope.orange_radius_m,
                    "multiplier": envelope.condition_multiplier
                }
            ))

    # 3. Overspeed rules
    if state == MachineState.TRAVEL and frame.speed_kmh > envelope.speed_cap_kmh:
        events.append(Event(
            event_id=str(uuid.uuid4()),
            type="OVERSPEED_CONDITION",
            severity=AlertSeverity.WARNING,
            machine_id=frame.machine_id,
            operator_id=frame.operator_id,
            site_id=site_id,
            ts=frame.ts,
            source_engine="safety@1.0",
            evidence={
                "speed_kmh": frame.speed_kmh,
                "cap_kmh": envelope.speed_cap_kmh,
                "multiplier": envelope.condition_multiplier
            }
        ))
        
    return events
