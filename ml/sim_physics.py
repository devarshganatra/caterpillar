"""
Deterministic, numpy-based re-implementation of simulator/sim.py's per-tick
physics (advance_state), used to generate synthetic frame-level data for
historical windows. Kept structurally parallel to simulator/sim.py's targets
and smoothing so live telemetry and generated history land in the same
feature distribution (checked by ml/tests/test_sim_parity.py).

This is a SEPARATE implementation, not an import of simulator/sim.py,
because sim.py is stateful/async and tied to httpx — but the formulas here
must be kept in sync with it by hand whenever sim.py's physics change.
"""
from __future__ import annotations

import numpy as np


def simulate_window(
    rng: np.random.Generator,
    cfg: dict,
    mode: str,
    n_frames: int,
    start_temp_c: float = 85.0,
    target_temp_override: float | None = None,
    cycle_duration_range: tuple[int, int] = (45, 90),
    fuel_multiplier: float = 1.0,
    truck_present: bool = True,
    hauler_queue_len: int = 1,
    machine_fault: bool = False,
    init_state: dict | None = None,
) -> tuple[dict[str, np.ndarray], dict]:
    """
    Generates n_frames of synthetic telemetry for one machine in one mode,
    mirroring simulator/sim.py::advance_state. Returns
    (frames, final_state) — frames are raw per-frame arrays (not yet reduced
    to window features — call ml.features.compute_window_features on them),
    and final_state is {rpm, speed, hyd, fuel, temp, cycle_timer,
    cycle_duration} to pass as `init_state` to the NEXT window's call, so
    physical state (and its variance) is continuous across windows within a
    task/day rather than spinning up from a cold 0 start every 300 frames.
    `mode` is "idle" | "travel" | "working".

    cfg: a dict subset of contracts.machine_config.MachineConfig
      {rpm_idle_range, rpm_working_range, hyd_working_range,
       speed_max_kmh, fuel_rate_idle_lph, fuel_rate_working_lph}
    """
    rpm_idle_lo, rpm_idle_hi = cfg["rpm_idle_range"]
    rpm_work_lo, rpm_work_hi = cfg["rpm_working_range"]

    if mode == "idle":
        target_rpm = (rpm_idle_lo + rpm_idle_hi) / 2
        target_speed = 0.0
        target_hyd = 30.0
    elif mode == "travel":
        target_rpm = rpm_work_lo + (rpm_work_hi - rpm_work_lo) * 0.3
        target_speed = cfg["speed_max_kmh"] * 0.6
        target_hyd = 80.0
    else:  # working
        target_rpm = (rpm_work_lo + rpm_work_hi) / 2
        target_speed = 0.5
        target_hyd = 250.0

    rpm = np.empty(n_frames)
    speed = np.empty(n_frames)
    hyd = np.empty(n_frames)
    fuel = np.empty(n_frames)
    temp = np.empty(n_frames)
    cycle_completed = np.zeros(n_frames, dtype=bool)

    if init_state is not None:
        cur_rpm = init_state.get("rpm", 0.0)
        cur_speed = init_state.get("speed", 0.0)
        cur_hyd = init_state.get("hyd", 0.0)
        cur_fuel = init_state.get("fuel", 0.0)
        cur_temp = init_state.get("temp", start_temp_c)
        cycle_timer = init_state.get("cycle_timer", 0)
        cycle_duration = init_state.get(
            "cycle_duration", int(rng.integers(cycle_duration_range[0], cycle_duration_range[1] + 1))
        )
    else:
        cur_rpm, cur_speed, cur_hyd, cur_fuel, cur_temp = 0.0, 0.0, 0.0, 0.0, start_temp_c
        cycle_timer = 0
        cycle_duration = int(rng.integers(cycle_duration_range[0], cycle_duration_range[1] + 1))

    target_temp = target_temp_override if target_temp_override is not None else (
        85.0 if mode != "idle" else 60.0
    )
    if machine_fault:
        target_temp = max(target_temp, 108.0)  # forces engine_temp_c > 105 threshold

    for i in range(n_frames):
        cur_rpm += (target_rpm - cur_rpm) * 0.1 + rng.normal(0, 30)
        cur_rpm = max(0.0, min(rpm_work_hi + 100, cur_rpm))

        cur_speed += (target_speed - cur_speed) * 0.2 + rng.normal(0, 0.3)
        cur_speed = max(0.0, min(cfg["speed_max_kmh"], cur_speed))

        cur_hyd += (target_hyd - cur_hyd) * 0.2 + rng.normal(0, 5.0)
        cur_hyd = max(0.0, cur_hyd)
        if machine_fault:
            cur_hyd = max(cur_hyd, 325.0)  # forces hydraulic_pressure_bar > 320 threshold

        rpm_ratio = max(0.0, (cur_rpm - rpm_idle_lo) / (rpm_work_hi - rpm_idle_lo))
        target_fuel = cfg["fuel_rate_idle_lph"] + (cfg["fuel_rate_working_lph"] - cfg["fuel_rate_idle_lph"]) * rpm_ratio
        cur_fuel += (target_fuel - cur_fuel) * 0.1 + rng.normal(0, 0.5)
        cur_fuel = max(0.0, cur_fuel)
        # fuel_multiplier (HIGH_FUEL_PER_CYCLE anomaly injection) scales only
        # the REPORTED reading for this frame, not the persistent EMA state —
        # applying it to cur_fuel itself would compound every frame
        # (fuel_multiplier ** n_frames), blowing up to astronomical values
        # over a 300-frame window.
        reported_fuel = cur_fuel * fuel_multiplier

        cur_temp += (target_temp - cur_temp) * 0.01 + rng.normal(0, 0.1)

        if mode == "working":
            cycle_timer += 1
            if cycle_timer >= cycle_duration:
                cycle_completed[i] = True
                cycle_timer = 0
                cycle_duration = int(rng.integers(cycle_duration_range[0], cycle_duration_range[1] + 1))

        rpm[i], speed[i], hyd[i], fuel[i], temp[i] = cur_rpm, cur_speed, cur_hyd, reported_fuel, cur_temp

    frames = {
        "engine_rpm": rpm,
        "speed_kmh": speed,
        "hydraulic_pressure_bar": hyd,
        "fuel_rate_lph": fuel,
        "engine_temp_c": temp,
        "cycle_completed": cycle_completed,
        "is_idle": np.full(n_frames, mode == "idle", dtype=bool),
        "truck_present": np.full(n_frames, truck_present, dtype=bool),
        "hauler_queue_len": np.full(n_frames, hauler_queue_len, dtype=float),
    }
    final_state = {
        "rpm": cur_rpm, "speed": cur_speed, "hyd": cur_hyd, "fuel": cur_fuel, "temp": cur_temp,
        "cycle_timer": cycle_timer, "cycle_duration": cycle_duration,
    }
    return frames, final_state
