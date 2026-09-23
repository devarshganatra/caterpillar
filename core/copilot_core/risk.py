"""
Risk Trajectory Engine (hot)
Calculates composite risk momentum.
"""
import math
from contracts.events import Event

def update_risk(
    current_risk: float,
    current_level: str,
    events: list[Event],
    dt_seconds: float,
    envelope_multiplier: float,
    half_life_seconds: float = 300.0
) -> tuple[float, str]:
    """
    Returns (new_risk_score, new_risk_level)
    """
    # 1. Decay
    # lambda = ln(2) / half_life
    decay_lambda = 0.69314718056 / half_life_seconds
    decayed_risk = current_risk * math.exp(-decay_lambda * dt_seconds)
    
    # 2. Add event weights
    added_risk = 0.0
    for e in events:
        w = 0.0
        if e.type == "SEATBELT_VIOLATION":
            w = 25.0
        elif e.type == "PROXIMITY_BREACH":
            if e.evidence.get("zone") == "RED":
                w = 30.0
            else:
                w = 10.0
        elif e.type == "OVERSPEED_CONDITION":
            w = 15.0
        elif e.type == "HEALTH_THRESHOLD":
            w = 10.0
            
        added_risk += w * envelope_multiplier
        
    # 3. Clamp 0-100
    new_risk = max(0.0, min(100.0, decayed_risk + added_risk))
    
    # 4. Level hysteresis
    # NORMAL | ELEVATED | HIGH
    # ELEVATED: enter >= 30, exit < 20
    # HIGH: enter >= 60, exit < 45
    new_level = current_level
    
    if current_level == "NORMAL":
        if new_risk >= 60.0:
            new_level = "HIGH"
        elif new_risk >= 30.0:
            new_level = "ELEVATED"
            
    elif current_level == "ELEVATED":
        if new_risk >= 60.0:
            new_level = "HIGH"
        elif new_risk < 20.0:
            new_level = "NORMAL"
            
    elif current_level == "HIGH":
        if new_risk < 45.0:
            # Drop to ELEVATED, but if it's < 20, drop all the way to NORMAL
            if new_risk < 20.0:
                new_level = "NORMAL"
            else:
                new_level = "ELEVATED"
                
    return new_risk, new_level
