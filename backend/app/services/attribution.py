"""
Deterministic idle attribution + operator-deviation detection (ARCH section
6.6). Pure functions except for reading the ModelRegistry's idle_baselines.
Never touches safety state — output is an IdleAttribution used by the warm
worker (Batch 3D) to emit IDLE_DEVIATION events for the correlator.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from backend.app.services.ml_registry import ModelRegistry, get_registry
from contracts.intelligence import IdleAttribution
from ml.features import robust_z

CAUSE_PRIORITY = ["PLANNED", "MACHINE", "WEATHER", "SITE", "OPERATOR"]


def classify_idle_frames(
    n_frames: int,
    ts_start: datetime,
    sim_seconds_per_frame: float,
    engine_temp_c: np.ndarray,
    hydraulic_pressure_bar: np.ndarray,
    truck_present: np.ndarray,
    hauler_queue_len: np.ndarray,
    context: dict,
    planned_breaks_utc: list[tuple[str, str]],
    fault_active: np.ndarray | bool,
    cfg: dict,
) -> np.ndarray:
    """
    Returns an array of length n_frames with one of CAUSE_PRIORITY per frame
    (first matching rule wins, in priority order):
      1. PLANNED  - frame timestamp falls in a configured break window
      2. MACHINE  - fault_active for this frame, OR engine_temp_c>105 / hyd>320
      3. WEATHER  - rainfall/visibility/wind/ground breach the weather-stop thresholds
      4. SITE     - no truck present OR queue length is 0
      5. OPERATOR - none of the above
    """
    causes = np.full(n_frames, "OPERATOR", dtype=object)

    if np.isscalar(fault_active):
        fault_arr = np.full(n_frames, bool(fault_active))
    else:
        fault_arr = np.asarray(fault_active, dtype=bool)

    weather_breach = (
        context.get("rainfall_mm_h", 0.0) >= cfg["weather_stop_rain_mm_h"]
        or context.get("visibility_m", 9999.0) < cfg["weather_stop_visibility_m"]
        or context.get("wind_kmh", 0.0) >= cfg["weather_stop_wind_kmh"]
        or context.get("ground") == "ICY"
    )

    for i in range(n_frames):
        ts = ts_start + timedelta(seconds=i * sim_seconds_per_frame)
        if _in_planned_break(ts, planned_breaks_utc):
            causes[i] = "PLANNED"
        elif fault_arr[i] or engine_temp_c[i] > 105 or hydraulic_pressure_bar[i] > 320:
            causes[i] = "MACHINE"
        elif weather_breach:
            causes[i] = "WEATHER"
        elif (not truck_present[i]) or hauler_queue_len[i] == 0:
            causes[i] = "SITE"
        else:
            causes[i] = "OPERATOR"

    return causes


def _in_planned_break(ts: datetime, breaks: list[tuple[str, str]]) -> bool:
    if not breaks:
        return False
    hm = ts.strftime("%H:%M")
    for start, end in breaks:
        if start <= hm < end:
            return True
    return False


def attribute_window(
    window_id: str, machine_id: str, operator_id: str,
    window_start: datetime, window_end: datetime,
    causes: np.ndarray, sim_seconds_per_frame: float,
    truck_present_ratio: float, hauler_queue_mean: float,
    context: dict, fault_event_ids: list[str], planned_break: bool,
) -> IdleAttribution:
    """Reduces a per-frame cause array into breakdown_s + primary_cause + evidence."""
    idle_seconds = float(len(causes)) * sim_seconds_per_frame
    breakdown_s = {}
    for cause in CAUSE_PRIORITY:
        breakdown_s[cause] = float(np.sum(causes == cause)) * sim_seconds_per_frame

    if idle_seconds > 0 and max(breakdown_s.values()) > 0:
        max_s = max(breakdown_s.values())
        # ties broken by CAUSE_PRIORITY order
        primary_cause = next(c for c in CAUSE_PRIORITY if breakdown_s[c] == max_s)
    else:
        primary_cause = None

    operator_idle_ratio = (
        breakdown_s.get("OPERATOR", 0.0) / idle_seconds if idle_seconds > 0 else 0.0
    )

    return IdleAttribution(
        window_id=window_id, machine_id=machine_id, operator_id=operator_id,
        window_start=window_start, window_end=window_end,
        idle_seconds=idle_seconds, breakdown_s=breakdown_s, primary_cause=primary_cause,
        evidence={
            "truck_present_ratio": truck_present_ratio, "hauler_queue_mean": hauler_queue_mean,
            "weather": context.get("weather"), "rainfall_mm_h": context.get("rainfall_mm_h"),
            "visibility_m": context.get("visibility_m"),
            "fault_event_ids": fault_event_ids, "planned_break": planned_break,
        },
        operator_idle_ratio=round(operator_idle_ratio, 4),
    )


class DeviationResult:
    def __init__(self, status, expected_idle_ratio, deviation_ratio, z, baseline_level, flag, raw_flag):
        self.status = status
        self.expected_idle_ratio = expected_idle_ratio
        self.deviation_ratio = deviation_ratio
        self.robust_z = z
        self.baseline_level = baseline_level
        self.operator_deviation_flag = flag
        self.raw_flag = raw_flag


def _lookup_level(idle_baselines: dict, level: str, key: str, min_n: int) -> dict | None:
    entry = idle_baselines["levels"].get(level, {}).get(key)
    if entry and entry["n"] >= min_n:
        return entry
    return None


def operator_deviation(
    operator_idle_ratio: float, operator_id: str, task_type: str, weather: str, operator_skill: str,
    prev_consecutive_raw_flags: int, cfg: dict, registry: ModelRegistry | None = None,
) -> DeviationResult:
    """
    Hierarchical backoff: operator x context -> skill x context -> task_type
    -> global (ARCH 6.6). Deviation fires when actual/expected >= idle_dev_ratio
    AND robust_z > idle_dev_robust_z, persisting >= idle_dev_persist_windows
    consecutive windows.
    """
    registry = registry or get_registry()
    if registry.baselines_status != "READY" or not registry.idle_baselines:
        return DeviationResult("UNAVAILABLE", None, None, None, None, False, False)

    idle_baselines = registry.idle_baselines
    min_n = cfg["idle_baseline_min_n"]
    sep = idle_baselines.get("key_separator", "||")

    task_type = task_type or "NONE"
    candidates = [
        ("operator_context", sep.join([operator_id, task_type, weather, operator_skill])),
        ("skill_context", sep.join([operator_skill, task_type, weather])),
        ("task_type", task_type),
        ("global", "global"),
    ]

    entry, level_name = None, None
    for level, key in candidates:
        entry = _lookup_level(idle_baselines, level, key, min_n)
        if entry:
            level_name = level
            break

    if entry is None:
        return DeviationResult("UNAVAILABLE", None, None, None, None, False, False)

    expected = entry["ewma"]
    ratio = operator_idle_ratio / max(expected, 0.01)
    z = robust_z(operator_idle_ratio, entry["median"], entry["mad"])

    raw_flag = ratio >= cfg["idle_dev_ratio"] and z > cfg["idle_dev_robust_z"]
    consecutive = prev_consecutive_raw_flags + 1 if raw_flag else 0
    flag = raw_flag and consecutive >= cfg["idle_dev_persist_windows"]

    return DeviationResult(
        status="OK", expected_idle_ratio=round(expected, 4), deviation_ratio=round(ratio, 3),
        z=round(z, 3), baseline_level=level_name, flag=flag, raw_flag=raw_flag,
    )
