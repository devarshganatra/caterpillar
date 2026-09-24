from datetime import datetime, timezone
import uuid
from contracts.events import TelemetryFrame, MachineState, AlertSeverity, ContextFrame, Event
from core.copilot_core.state import classify_state
from core.copilot_core.envelope import calculate_envelope
from core.copilot_core.safety import evaluate_safety
from core.copilot_core.health import evaluate_health
from core.copilot_core.risk import update_risk
from core.copilot_core.arbitrator import arbitrate, AlertState
import pytest

pytestmark = pytest.mark.unit

def _frame(rpm=1500, hyd=200, speed=0, seatbelt="FASTENED", cycle=False, prox=None, temp=90):
    return TelemetryFrame(
        seq=1, ts=datetime.now(timezone.utc), machine_id="M1", operator_id="O1",
        engine_rpm=rpm, engine_temp_c=temp, hydraulic_pressure_bar=hyd,
        fuel_rate_lph=10, speed_kmh=speed, seatbelt=seatbelt,
        proximity=prox, cycle_completed=cycle, truck_present=False, hauler_queue_len=0,
        gps=(0,0), sig="test"
    )

def test_state():
    # WORKING is immediate
    f = _frame(rpm=1500, hyd=150, speed=0)
    s, d = classify_state(f, MachineState.IDLE, 0)
    assert s == MachineState.WORKING and d == 0
    
    # IDLE requires dwell
    f2 = _frame(rpm=800, hyd=40, speed=0)
    s2, d2 = classify_state(f2, MachineState.WORKING, 0)
    assert s2 == MachineState.WORKING and d2 == 1 # dwell 1
    s2, d2 = classify_state(f2, MachineState.WORKING, 4)
    assert s2 == MachineState.IDLE and d2 == 0 # dwell reached 5
    
def test_envelope():
    # Base
    e = calculate_envelope(None)
    assert e.red_radius_m == 4.0
    
    # Rain + Muddy -> max multiplier 1.3
    c = ContextFrame(ts=datetime.now(timezone.utc), site_id="S", weather="RAIN", rainfall_mm_h=10, visibility_m=1000, ambient_temp_c=20, wind_kmh=10, ground="MUDDY", daylight=True)
    e2 = calculate_envelope(c)
    assert e2.condition_multiplier == 1.3
    assert e2.red_radius_m == 5.2
    
def test_safety():
    e = calculate_envelope(None)
    f = _frame(seatbelt="UNFASTENED")
    evs = evaluate_safety(f, MachineState.WORKING, e, 0, "S1")
    assert len(evs) == 1
    assert evs[0].type == "SEATBELT_VIOLATION" and evs[0].severity == AlertSeverity.CRITICAL
    
def test_health():
    f = _frame(temp=110)
    evs = evaluate_health(f, "S1")
    assert len(evs) == 1
    assert evs[0].type == "HEALTH_THRESHOLD"

def test_risk():
    e = Event(event_id="1", type="SEATBELT_VIOLATION", severity=AlertSeverity.CRITICAL, machine_id="M", operator_id="O", site_id="S", ts=datetime.now(timezone.utc), source_engine="a", evidence={})
    r, lvl = update_risk(0.0, "NORMAL", [e], 1.0, 1.0)
    assert r == 25.0
    assert lvl == "NORMAL"
    r2, lvl2 = update_risk(r, "NORMAL", [e], 1.0, 1.0) # -> 50
    assert r2 > 30.0
    assert lvl2 == "ELEVATED"

test_state()
test_envelope()
test_safety()
test_health()
test_risk()
print("All engine tests passed!")
