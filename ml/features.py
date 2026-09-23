"""
Pure-numpy feature computation shared by training (ml/generate_history.py,
ml/train_*.py) and serving (backend/app/worker/warm.py in Batch 3D). No
pandas here — this module runs inside the warm worker's hot loop, so it
stays cheap and dependency-light.
"""
from __future__ import annotations

import numpy as np

from ml.constants import WINDOW_FEATURES, ETA_NUMERIC, ETA_CATEGORICAL


def compute_window_features(frames: dict[str, np.ndarray], sim_seconds_per_frame: float = 1.0) -> dict[str, float | None]:
    """
    Reduces a window's raw frame arrays into the WINDOW_FEATURES vector plus
    a few extra descriptive fields. Mirrors ARCH section 5.1's WindowAggregate list.

    Expected keys in `frames` (all same length = frame_count):
      engine_rpm, engine_temp_c, hydraulic_pressure_bar, fuel_rate_lph,
      speed_kmh, cycle_completed (bool), is_idle (bool),
      truck_present (bool), hauler_queue_len (int/float)

    fuel_per_cycle / cycle_time_mean / cycle_time_cv are None when there are
    fewer than 2 completed cycles in the window (can't compute a cycle time).
    """
    n = len(frames["engine_rpm"])
    out: dict[str, float | None] = {}

    if n == 0:
        return {k: None for k in WINDOW_FEATURES} | {
            "frame_count": 0, "cycle_count": 0, "working_ratio": None,
            "truck_present_ratio": None, "hauler_queue_mean": None, "fuel_l_total": None,
        }

    rpm = frames["engine_rpm"]
    temp = frames["engine_temp_c"]
    hyd = frames["hydraulic_pressure_bar"]
    fuel = frames["fuel_rate_lph"]
    cycle_completed = np.asarray(frames["cycle_completed"], dtype=bool)
    is_idle = np.asarray(frames.get("is_idle", np.zeros(n, dtype=bool)), dtype=bool)
    truck_present = np.asarray(frames.get("truck_present", np.zeros(n, dtype=bool)), dtype=bool)
    hauler_queue = np.asarray(frames.get("hauler_queue_len", np.zeros(n)), dtype=float)

    out["rpm_mean"] = float(np.mean(rpm))
    out["rpm_std"] = float(np.std(rpm))
    out["hyd_p95"] = float(np.percentile(hyd, 95))
    out["idle_ratio"] = float(np.mean(is_idle))
    out["truck_present_ratio"] = float(np.mean(truck_present))
    out["hauler_queue_mean"] = float(np.mean(hauler_queue))
    out["working_ratio"] = float(np.mean(hyd >= 120.0))

    fuel_l_total = float(np.sum(fuel) * sim_seconds_per_frame / 3600.0)  # L/h * s / 3600 = L
    out["fuel_l_total"] = fuel_l_total

    cycle_indices = np.flatnonzero(cycle_completed)
    cycle_count = int(len(cycle_indices))
    out["cycle_count"] = cycle_count

    if cycle_count >= 2:
        # cycle boundaries: time (in frames) between consecutive completions
        inter_cycle_frames = np.diff(cycle_indices)
        cycle_times_s = inter_cycle_frames.astype(float) * sim_seconds_per_frame
        out["cycle_time_mean"] = float(np.mean(cycle_times_s))
        cv_denom = out["cycle_time_mean"]
        out["cycle_time_cv"] = float(np.std(cycle_times_s) / cv_denom) if cv_denom > 0 else None
        out["fuel_per_cycle"] = fuel_l_total / cycle_count if cycle_count > 0 else None
    else:
        out["cycle_time_mean"] = None
        out["cycle_time_cv"] = None
        out["fuel_per_cycle"] = None

    # temp_slope: least-squares slope in degC per simulated MINUTE
    t_minutes = np.arange(n, dtype=float) * sim_seconds_per_frame / 60.0
    if n >= 2 and np.ptp(t_minutes) > 0:
        slope, _intercept = np.polyfit(t_minutes, temp, 1)
        out["temp_slope"] = float(slope)
    else:
        out["temp_slope"] = 0.0

    out["frame_count"] = n
    return out


def robust_z(x: float, median: float, mad: float) -> float:
    """(x - median) / (1.4826 * mad); returns 0.0 when mad == 0 (degenerate distribution)."""
    if mad == 0:
        return 0.0
    return (x - median) / (1.4826 * mad)


def eta_feature_row(task_ctx: dict) -> dict:
    """
    Normalizes a task-context dict into the exact keys ETA_NUMERIC +
    ETA_CATEGORICAL expect, filling defaults for anything missing and
    recording which fields were defaulted (used by the serving layer to
    report `defaults_used`).
    """
    row = {}
    defaults_used = []

    numeric_defaults = {
        "target_cycles": 50, "machine_age_years": 3, "rainfall_mm_h": 0.0,
        "visibility_m": 9999.0, "wind_kmh": 5.0, "ambient_temp_c": 22.0,
        "hauler_queue_mean": 1.0,
    }
    for key in ETA_NUMERIC:
        if key in task_ctx and task_ctx[key] is not None:
            row[key] = float(task_ctx[key])
        else:
            row[key] = numeric_defaults[key]
            defaults_used.append(key)

    categorical_defaults = {
        "task_type": "TRUCK_LOADING", "machine_type": "EXCAVATOR",
        "operator_skill": "INTERMEDIATE", "weather": "SUNNY", "ground": "DRY",
    }
    for key in ETA_CATEGORICAL:
        if key in task_ctx and task_ctx[key] is not None:
            row[key] = task_ctx[key]
        else:
            row[key] = categorical_defaults[key]
            defaults_used.append(key)

    row["_defaults_used"] = defaults_used
    return row


def encode_eta_row(row: dict, feature_columns: list[str]) -> np.ndarray:
    """
    One-hot + numeric encoding in a FIXED column order (feature_columns,
    as stored in eta_meta.json), so training and serving always agree.
    """
    values = []
    for col in feature_columns:
        if col in ETA_NUMERIC:
            values.append(float(row.get(col, 0.0)))
        else:
            # one-hot column name convention: "{field}__{level}"
            field, _, level = col.partition("__")
            values.append(1.0 if row.get(field) == level else 0.0)
    return np.array(values, dtype=float)


def eta_feature_columns() -> list[str]:
    """The canonical, stable feature column order used by encode_eta_row / training."""
    cols = list(ETA_NUMERIC)
    for field in sorted(ETA_CATEGORICAL.keys()):
        for level in ETA_CATEGORICAL[field]:
            cols.append(f"{field}__{level}")
    return cols
