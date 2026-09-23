"""
Contracts for the Stage 3 intelligence layer (ETA, idle attribution, anomaly
detection). Additive to contracts/events.py — nothing here is consumed by
the hot path.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class EtaFactor(BaseModel):
    group: str
    minutes: float
    top_feature: str
    top_feature_value: str | float | None = None


class EtaEstimate(BaseModel):
    task_id: str
    machine_id: str
    window_id: Optional[str] = None
    ts: datetime

    status: Literal["OK", "UNAVAILABLE"] = "OK"
    unavailable_reason: Optional[str] = None
    model_version: Optional[str] = None

    baseline_p10_min: Optional[float] = None
    baseline_p50_min: Optional[float] = None
    baseline_p90_min: Optional[float] = None

    remaining_p10_min: Optional[float] = None
    remaining_p50_min: Optional[float] = None
    remaining_p90_min: Optional[float] = None
    eta_total_p50_min: Optional[float] = None
    progress: Optional[float] = None
    blend_weight_model: Optional[float] = None

    cycles_done: Optional[int] = None
    target_cycles: Optional[int] = None
    planner_estimate_min: Optional[float] = None

    factors: list[EtaFactor] = []
    base_value_min: Optional[float] = None
    defaults_used: list[str] = []

    slip_pct: Optional[float] = None
    time_unit: Literal["sim_minutes"] = "sim_minutes"


IdleCause = Literal["PLANNED", "MACHINE", "WEATHER", "SITE", "OPERATOR"]


class IdleAttribution(BaseModel):
    window_id: str
    machine_id: str
    operator_id: str
    window_start: datetime
    window_end: datetime

    idle_seconds: float
    breakdown_s: dict[str, float] = {}  # keys are IdleCause values
    primary_cause: Optional[IdleCause] = None
    evidence: dict = {}
    operator_idle_ratio: float

    deviation_status: Literal["OK", "UNAVAILABLE"] = "OK"
    expected_idle_ratio: Optional[float] = None
    deviation_ratio: Optional[float] = None
    robust_z: Optional[float] = None
    baseline_level: Optional[str] = None
    operator_deviation_flag: bool = False
    consecutive_windows: int = 0


class AnomalyDriver(BaseModel):
    feature: str
    value: float
    robust_z: float
    shap: Optional[float] = None


class IncidentSummary(BaseModel):
    """Compact incident view for UI pushes / list endpoints (Batch 3E/3F)."""
    id: str
    machine_id: str
    site_id: str
    operator_id: Optional[str] = None
    task_id: Optional[str] = None
    category: str
    severity: Literal["INFO", "WARNING", "CRITICAL"]
    escalated: bool = False
    status: Literal["OPEN", "ACKNOWLEDGED", "CLOSED"]
    opened_at: datetime
    last_event_at: datetime
    event_count: int
    explanation_status: Literal["PENDING", "READY", "FALLBACK", "FAILED"] = "PENDING"


class TimelineEntry(BaseModel):
    entry_key: str
    kind: Literal["EVENT", "STATUS", "EXPLANATION"]
    event_type: Optional[str] = None
    severity: Optional[str] = None
    first_ts: datetime
    last_ts: datetime
    count: int
    representative_event_id: Optional[str] = None
    summary: str
    actor_id: Optional[str] = None


class IncidentExplanation(BaseModel):
    """API-facing model for a persisted incident explanation (Batch 3H)."""
    source: Literal["GROQ", "FALLBACK"]
    model_name: Optional[str] = None
    summary: str
    probable_causes: list[dict]
    recommended_actions: list[dict]
    lesson: dict
    training_refs: list[str] = []
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    fallback_reason: Optional[str] = None
    created_at: datetime


class AnomalyResult(BaseModel):
    window_id: str
    machine_id: str
    method: Literal["IFOREST", "ROBUST_Z", "SKIPPED", "UNAVAILABLE"]
    reason: Optional[str] = None
    score: Optional[float] = None
    threshold: Optional[float] = None
    is_anomalous: bool = False
    drivers: list[AnomalyDriver] = []
    drivers_method: Optional[str] = None
    model_version: Optional[str] = None
