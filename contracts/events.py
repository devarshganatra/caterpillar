from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal, Optional, List, Dict, Any
from enum import Enum

class MachineState(str, Enum):
    OFF = "OFF"
    IDLE = "IDLE"
    TRAVEL = "TRAVEL"
    WORKING = "WORKING"

class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

class UiPushType(str, Enum):
    state_change = "state_change"
    alert = "alert"
    alert_clear = "alert_clear"
    risk = "risk"
    envelope = "envelope"
    eta = "eta"
    idle_attribution = "idle_attribution"
    anomaly = "anomaly"
    lesson_ready = "lesson_ready"
    incident = "incident"

class EventType(str, Enum):
    """
    Canonical event type strings. Event.type stays a plain str for forward
    compatibility (new engines can emit new types without a contract change),
    but this enum is the reference list — use its values when emitting.
    """
    SEATBELT_VIOLATION = "SEATBELT_VIOLATION"
    PROXIMITY_BREACH = "PROXIMITY_BREACH"
    PROXIMITY_PERSISTENT = "PROXIMITY_PERSISTENT"
    OVERSPEED_CONDITION = "OVERSPEED_CONDITION"
    HEALTH_THRESHOLD = "HEALTH_THRESHOLD"
    IDLE_DEVIATION = "IDLE_DEVIATION"
    OPERATIONAL_ANOMALY = "OPERATIONAL_ANOMALY"
    ETA_SLIP = "ETA_SLIP"
    RISK_LEVEL_CHANGE = "RISK_LEVEL_CHANGE"
    FATIGUE_INDICATOR = "FATIGUE_INDICATOR"
    CHECKLIST_FAIL = "CHECKLIST_FAIL"

class SeqValidationResult(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"      # seq == last_seen
    OUT_OF_ORDER = "out_of_order" # seq < last_seen (not duplicate)
    GAP = "gap"                   # seq > last_seen + 1 -> log, don't reject
    FIRST = "first"               # no prior seq for this machine

class ProximityReading(BaseModel):
    zone: Literal["RED", "ORANGE", "YELLOW"]
    distance_m: float
    source: str

class TelemetryFrame(BaseModel):
    seq: int
    ts: datetime
    machine_id: str
    operator_id: str
    task_id: Optional[str] = None

    engine_rpm: float
    engine_temp_c: float
    hydraulic_pressure_bar: float
    fuel_rate_lph: float
    speed_kmh: float

    seatbelt: Literal["FASTENED", "UNFASTENED"]

    proximity: Optional[ProximityReading] = None
    cycle_completed: bool
    truck_present: bool
    hauler_queue_len: int

    gps: tuple[float, float]
    sig: str

class ContextFrame(BaseModel):
    ts: datetime
    site_id: str
    weather: Literal["SUNNY", "CLOUDY", "WINDY", "RAIN"]
    rainfall_mm_h: float
    visibility_m: float
    ambient_temp_c: float
    wind_kmh: float
    ground: Literal["DRY", "WET", "MUDDY", "ICY"]
    daylight: bool

class UiPush(BaseModel):
    type: UiPushType
    machine_id: str
    ts: datetime
    payload: dict

class Event(BaseModel):
    event_id: str
    type: str
    severity: AlertSeverity
    machine_id: str
    operator_id: str
    site_id: str
    ts: datetime
    source_engine: str
    evidence: dict

class Incident(BaseModel):
    id: str
    machine_id: str
    operator_id: str
    severity: AlertSeverity
    category: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    timeline: list
    explanation: Optional[dict] = None
    lesson_id: Optional[str] = None

class AuditEntry(BaseModel):
    id: str
    actor_id: str
    action: str
    target_type: str
    target_id: str
    payload_hash: str
    prev_hash: str
    ts: datetime
