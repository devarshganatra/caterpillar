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
