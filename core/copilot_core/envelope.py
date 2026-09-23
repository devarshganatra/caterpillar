"""
Condition-Adaptive Safety Envelope (hot)
Adjusts base safety parameters using context/condition multipliers.
"""
from dataclasses import dataclass
from contracts.events import ContextFrame

@dataclass
class SafetyEnvelope:
    red_radius_m: float
    orange_radius_m: float
    speed_cap_kmh: float
    condition_multiplier: float
    active_conditions: list[str]

def calculate_envelope(context: ContextFrame | None) -> SafetyEnvelope:
    # Base parameters
    red = 4.0
    orange = 7.0
    speed = 6.0
    
    if not context:
        return SafetyEnvelope(
            red_radius_m=red,
            orange_radius_m=orange,
            speed_cap_kmh=speed,
            condition_multiplier=1.0,
            active_conditions=[]
        )
        
    conditions = []
    max_radius_mult = 1.0
    min_speed_cap = speed
    
    if context.weather == "RAIN" or context.rainfall_mm_h > 0:
        conditions.append("RAIN")
        max_radius_mult = max(max_radius_mult, 1.3)
        min_speed_cap = min(min_speed_cap, 4.0)
        
    if (context.weather == "CLOUDY" and not context.daylight) or context.visibility_m < 50:
        conditions.append("LOW_VIS")
        max_radius_mult = max(max_radius_mult, 1.5)
        min_speed_cap = min(min_speed_cap, 3.0)
        
    if context.ground == "MUDDY":
        conditions.append("MUDDY")
        max_radius_mult = max(max_radius_mult, 1.2)
        min_speed_cap = min(min_speed_cap, 3.0)
        
    if context.ground == "ICY":
        conditions.append("ICY")
        max_radius_mult = max(max_radius_mult, 1.5)
        min_speed_cap = min(min_speed_cap, 2.0)
        
    if context.wind_kmh > 40 or context.weather == "WINDY":
        conditions.append("WINDY")
        min_speed_cap = min(min_speed_cap, 5.0)
        
    return SafetyEnvelope(
        red_radius_m=round(red * max_radius_mult, 1),
        orange_radius_m=round(orange * max_radius_mult, 1),
        speed_cap_kmh=round(min_speed_cap, 1),
        condition_multiplier=max_radius_mult,
        active_conditions=conditions
    )
