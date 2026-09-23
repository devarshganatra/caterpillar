"""
Machine State Classifier (hot path)
Deterministic rules with dwell and asymmetric hysteresis to classify machine state.
"""
from contracts.events import TelemetryFrame, MachineState

def classify_state(
    frame: TelemetryFrame,
    previous_state: MachineState,
    dwell_ticks: int
) -> tuple[MachineState, int]:
    """
    Returns (new_state, new_dwell_ticks).
    
    Hysteresis logic:
    - Entering TRAVEL or WORKING is immediate (dwell=0).
    - Entering OFF is immediate.
    - Exiting TRAVEL/WORKING to IDLE requires 5 consecutive ticks of IDLE conditions.
    """
    rpm = frame.engine_rpm
    hyd = frame.hydraulic_pressure_bar
    speed = frame.speed_kmh
    
    # 1. Evaluate current tick conditions
    cond_off = rpm == 0
    cond_travel = speed >= 0.5
    cond_working = hyd >= 120 or frame.cycle_completed
    cond_idle = rpm > 0 and rpm < 1000 and hyd < 50 and speed < 0.5
    
    # 2. Priority: OFF > WORKING > TRAVEL > IDLE
    # Note: architecture specifies WORKING/TRAVEL are immediate.
    if cond_off:
        return MachineState.OFF, 0
        
    if cond_working:
        return MachineState.WORKING, 0
        
    if cond_travel:
        return MachineState.TRAVEL, 0
        
    # 3. IDLE condition met?
    if cond_idle:
        if previous_state == MachineState.IDLE:
            return MachineState.IDLE, 0
        
        # We are not in IDLE, but IDLE conditions are met -> increment dwell
        new_dwell = dwell_ticks + 1
        if new_dwell >= 5:
            # Dwell satisfied, transition to IDLE
            return MachineState.IDLE, 0
        else:
            # Stay in previous state until dwell is satisfied
            return previous_state, new_dwell
            
    # 4. If no clear condition is met (e.g. rpm 1200, hyd 80, speed 0)
    # The machine is transitioning or in an ambiguous state.
    # Architecture fallback: maintain previous state, reset dwell.
    return previous_state, 0
