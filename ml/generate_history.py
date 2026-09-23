"""
Deterministic synthetic historical dataset generator.

Builds a day-by-day timeline per machine: tasks (WORKING windows) separated
by idle gaps (PLANNED / MACHINE / WEATHER / SITE / OPERATOR — see
ml.constants.IDLE_CAUSES), each gap generated with signals that directly
satisfy the attribution priority rule implemented in Batch 3C, so the
generator IS the ground truth for idle-attribution accuracy evaluation.

Usage:
    PYTHONPATH=. venv/bin/python -m ml.generate_history --profile dev --seed 42 --out ml/data

Never holds frame-level data for more than one window at a time — each
window's ~300 frames are reduced to a feature row via
ml.features.compute_window_features immediately and then discarded.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from contracts.machine_config import MACHINES
from ml.constants import (
    TASK_TYPES, WEATHER_M, SKILL_M, GROUND_M, PLANNER_SKILL_M,
    WEATHER_LEVELS, GROUND_LEVELS, IDLE_CAUSES, ANOMALY_TYPES, ANOMALY_RATE,
    N_OPERATORS, OPERATOR_SKILL_MIX, SITES, GENERATOR_VERSION,
)
from ml.features import compute_window_features
from ml.sim_physics import simulate_window

WINDOW_S = 300  # "5 min real" per ARCHITECTURE_new.md section 5.1 WindowAggregate grain
DAY_LENGTH_S = 9 * 3600  # one shift
PLANNED_BREAK_AT_S = 4 * 3600
PLANNED_BREAK_DURATION_S = 1800

PROFILES = {"tiny": 3, "dev": 15, "full": 60}


@dataclass
class Operator:
    operator_id: str
    skill: str
    high_idle: bool


def build_operators() -> list[Operator]:
    ops = []
    for i in range(N_OPERATORS):
        ops.append(Operator(
            operator_id=f"OP-H{i+1:02d}",
            skill=OPERATOR_SKILL_MIX[i],
            high_idle=(i == 0),  # OP-H01 is the deterministic heavy-tailed operator
        ))
    return ops


def draw_weather(rng: np.random.Generator) -> dict:
    weather = rng.choice(WEATHER_LEVELS, p=[0.50, 0.25, 0.15, 0.10])
    if weather == "RAIN":
        rainfall = float(rng.uniform(2, 35))
        visibility = float(rng.uniform(80, 400))
        wind = float(rng.uniform(5, 30))
        ground = "MUDDY" if rainfall >= 10 else "WET"
    elif weather == "WINDY":
        rainfall = 0.0
        visibility = float(rng.uniform(500, 3000))
        wind = float(rng.uniform(30, 60))
        ground = rng.choice(["DRY", "WET"], p=[0.8, 0.2])
    else:
        rainfall = 0.0
        visibility = float(rng.uniform(800, 5000))
        wind = float(rng.uniform(0, 15))
        ground = rng.choice(["DRY", "WET"], p=[0.85, 0.15])
    ambient = float(rng.uniform(10, 34))
    return {"weather": str(weather), "rainfall_mm_h": rainfall, "visibility_m": visibility,
            "wind_kmh": wind, "ambient_temp_c": ambient, "ground": str(ground)}


def draw_gap_cause_and_duration(rng: np.random.Generator, operator: Operator, machine_age: int,
                                 hauler_count: int, weather_ctx: dict) -> tuple[str, float]:
    heavy_rain = weather_ctx["rainfall_mm_h"] >= 20.0 or weather_ctx["visibility_m"] < 50.0
    w_machine = 0.03 + 0.01 * machine_age  # older machines fault more often
    w_weather = 0.25 if heavy_rain else 0.03
    w_operator = 0.15 * (3.0 if operator.high_idle else 1.0)
    w_site = 1.0
    weights = np.array([w_machine, w_weather, w_site, w_operator])
    weights = weights / weights.sum()
    cause = rng.choice(["MACHINE", "WEATHER", "SITE", "OPERATOR"], p=weights)

    if cause == "MACHINE":
        minutes = float(rng.uniform(5, 15))
    elif cause == "WEATHER":
        minutes = float(rng.uniform(15, 45)) if heavy_rain else float(rng.uniform(5, 15))
    elif cause == "SITE":
        minutes = float(rng.gamma(shape=2.0, scale=5.0 / hauler_count))
        minutes = max(1.0, minutes)
    else:  # OPERATOR
        mean_log = np.log(20.0) if operator.high_idle else np.log(8.0)
        minutes = float(rng.lognormal(mean=mean_log, sigma=0.8))
        minutes = min(minutes, 90.0)  # cap runaway lognormal tail
    return str(cause), minutes * 60.0


def _gap_signal_kwargs(cause: str) -> dict:
    """Maps an idle cause to the physical signals that make Batch 3C's
    priority-based attribution recover the SAME cause from the window."""
    if cause == "MACHINE":
        return dict(machine_fault=True, truck_present=True, hauler_queue_len=1)
    if cause == "WEATHER":
        return dict(machine_fault=False, truck_present=True, hauler_queue_len=1)
    if cause == "SITE":
        return dict(machine_fault=False, truck_present=False, hauler_queue_len=0)
    # OPERATOR: truck present, queue nonzero, no fault -> none of the other
    # priority rules match, so OPERATOR is what's left (matches ARCH 6.6).
    return dict(machine_fault=False, truck_present=True, hauler_queue_len=1)


def generate_windows_for_span(
    rng: np.random.Generator, cfg: dict, mode: str, total_s: float,
    window_rows: list[dict], base_row: dict, gap_cause: str | None = None,
    anomaly_pool_eligible: bool = False, phys_state: dict | None = None,
) -> dict:
    """
    Chops a span of `total_s` seconds into WINDOW_S windows, simulates each,
    reduces to features, and appends a row to window_rows. Frame arrays are
    discarded immediately after each window (never accumulated). `phys_state`
    (rpm/speed/hyd/fuel/temp/cycle_timer/cycle_duration) is threaded from one
    window to the next so physical state is continuous across window and
    task/gap boundaries within a day, instead of a spin-up transient from a
    cold 0 start every single window (which would badly inflate variance
    features like rpm_std relative to real, continuously-running telemetry —
    caught by ml/tests/test_sim_parity.py). Returns the updated phys_state
    for the caller to pass into the next span.
    """
    remaining = total_s
    while remaining > 0:
        n_s = min(WINDOW_S, remaining)
        n_frames = max(1, int(round(n_s)))

        anomaly_type = None
        sim_kwargs = {}
        if gap_cause is not None:
            sim_kwargs.update(_gap_signal_kwargs(gap_cause))
        else:
            sim_kwargs.update(truck_present=True, hauler_queue_len=1)

        if mode == "working" and anomaly_pool_eligible and rng.random() < ANOMALY_RATE:
            anomaly_type = str(rng.choice(ANOMALY_TYPES))
            if anomaly_type == "HIGH_FUEL_PER_CYCLE":
                sim_kwargs["fuel_multiplier"] = 1.6
            elif anomaly_type == "ERRATIC_CYCLES":
                sim_kwargs["cycle_duration_range"] = (20, 150)
            elif anomaly_type == "HOT_ENGINE":
                sim_kwargs["target_temp_override"] = 100.0

        frames, phys_state = simulate_window(rng, cfg, mode, n_frames, init_state=phys_state, **sim_kwargs)
        feats = compute_window_features(frames, sim_seconds_per_frame=1.0)

        is_anomaly = bool(anomaly_type is not None and (feats.get("cycle_count") or 0) >= 2)

        row = dict(base_row)
        row.update(feats)
        row["window_s"] = n_frames
        row["is_anomaly"] = int(is_anomaly)
        row["anomaly_type"] = anomaly_type if is_anomaly else ""
        for c in IDLE_CAUSES:
            row[f"idle_s_{c}"] = float(n_frames) if (mode == "idle" and c == gap_cause) else 0.0
        window_rows.append(row)

        remaining -= n_s

    return phys_state


def generate(profile: str, seed: int, out_dir: str) -> dict:
    t0 = time.time()
    n_days = PROFILES[profile]
    rng = np.random.default_rng(seed)

    operators = build_operators()
    machines = list(MACHINES.values())

    task_rows: list[dict] = []
    window_rows: list[dict] = []
    weather_rows: list[dict] = []

    train_end_day = int(n_days * 0.7)
    val_end_day = int(n_days * 0.8)

    for day in range(n_days):
        split = "train" if day < train_end_day else ("val" if day < val_end_day else "test")

        day_weather: dict[str, dict] = {}
        for site_id in SITES:
            ctx = draw_weather(rng)
            day_weather[site_id] = ctx
            weather_rows.append({"day": day, "site_id": site_id, "split": split, **ctx})

        for machine in machines:
            cfg = {
                "rpm_idle_range": machine.rpm_idle_range,
                "rpm_working_range": machine.rpm_working_range,
                "hyd_working_range": machine.hyd_working_range,
                "speed_max_kmh": machine.speed_max_kmh,
                "fuel_rate_idle_lph": machine.fuel_rate_idle_lph,
                "fuel_rate_working_lph": machine.fuel_rate_working_lph,
            }
            weather_ctx = day_weather[machine.site_id]
            hauler_count = SITES[machine.site_id]["hauler_count"]

            eligible_types = [t for t, spec in TASK_TYPES.items() if machine.type in spec["machine_types"]]

            elapsed = 0.0
            planned_break_done = False
            n_tasks_target = int(rng.poisson(14))
            # Physical state (rpm/hyd/fuel/temp/...) carried across windows
            # for this machine's whole day, reset only at day start (a cold
            # shift-start), not per-window — see generate_windows_for_span docstring.
            phys_state = None

            task_idx = 0
            while elapsed < DAY_LENGTH_S and task_idx < max(1, n_tasks_target):
                if not planned_break_done and elapsed >= PLANNED_BREAK_AT_S:
                    operator = operators[rng.integers(0, N_OPERATORS)]
                    base_row = _window_base_row(machine, weather_ctx, operator, None, None, day, split)
                    phys_state = generate_windows_for_span(rng, cfg, "idle", PLANNED_BREAK_DURATION_S,
                                                            window_rows, base_row, gap_cause="PLANNED",
                                                            phys_state=phys_state)
                    elapsed += PLANNED_BREAK_DURATION_S
                    planned_break_done = True
                    continue

                operator = operators[rng.integers(0, N_OPERATORS)]
                task_type = str(rng.choice(eligible_types))
                target_cycles = int(rng.integers(20, 81))
                cycle_s = TASK_TYPES[task_type]["cycle_s"]
                base_min = target_cycles * cycle_s / 60.0

                weather_m = WEATHER_M[weather_ctx["weather"]]
                ground_m = GROUND_M[weather_ctx["ground"]]
                skill_m = SKILL_M[operator.skill]
                queue_delay_min = float(rng.gamma(shape=2.0, scale=5.0 / hauler_count))
                noise = float(rng.lognormal(mean=0.0, sigma=0.08))

                actual_min = base_min * weather_m * ground_m * skill_m * (1 + 0.03 * machine.age_years) * noise
                actual_min += queue_delay_min
                planner_estimate_min = base_min * PLANNER_SKILL_M[operator.skill]

                task_id = f"HIST-{machine.machine_id}-{day:03d}-{task_idx:03d}"
                task_rows.append({
                    "task_id": task_id, "day": day, "split": split,
                    "machine_id": machine.machine_id, "site_id": machine.site_id,
                    "machine_type": machine.type, "machine_age_years": machine.age_years,
                    "operator_id": operator.operator_id, "operator_skill": operator.skill,
                    "task_type": task_type, "target_cycles": target_cycles,
                    "weather": weather_ctx["weather"], "rainfall_mm_h": weather_ctx["rainfall_mm_h"],
                    "visibility_m": weather_ctx["visibility_m"], "wind_kmh": weather_ctx["wind_kmh"],
                    "ambient_temp_c": weather_ctx["ambient_temp_c"], "ground": weather_ctx["ground"],
                    "hauler_queue_mean": 1.0,
                    "actual_min": actual_min, "planner_estimate_min": planner_estimate_min,
                    "queue_delay_min": queue_delay_min,
                })

                base_row = _window_base_row(machine, weather_ctx, operator, task_id, task_type, day, split)
                base_row["target_cycles"] = target_cycles
                phys_state = generate_windows_for_span(rng, cfg, "working", actual_min * 60.0,
                                                        window_rows, base_row, gap_cause=None,
                                                        anomaly_pool_eligible=True, phys_state=phys_state)
                elapsed += actual_min * 60.0
                task_idx += 1

                if elapsed >= DAY_LENGTH_S:
                    break

                gap_cause, gap_s = draw_gap_cause_and_duration(
                    rng, operator, machine.age_years, hauler_count, weather_ctx
                )
                base_row = _window_base_row(machine, weather_ctx, operator, None, None, day, split)
                phys_state = generate_windows_for_span(rng, cfg, "idle", gap_s, window_rows, base_row,
                                                        gap_cause=gap_cause, phys_state=phys_state)
                elapsed += gap_s

    tasks_df = pd.DataFrame(task_rows)
    windows_df = pd.DataFrame(window_rows)
    weather_df = pd.DataFrame(weather_rows)

    _write_gz_csv(tasks_df, f"{out_dir}/tasks.csv.gz")
    _write_gz_csv(windows_df, f"{out_dir}/windows.csv.gz")
    _write_gz_csv(weather_df, f"{out_dir}/weather.csv.gz")

    entities = {
        "machines": [m.machine_id for m in machines],
        "operators": [{"operator_id": o.operator_id, "skill": o.skill, "high_idle": o.high_idle} for o in operators],
        "sites": SITES,
    }
    with open(f"{out_dir}/entities.json", "w") as f:
        json.dump(entities, f, indent=2)

    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "profile": profile,
        "n_days": n_days,
        "split_boundaries": {"train_end_day": train_end_day, "val_end_day": val_end_day},
        "rows": {"tasks": len(tasks_df), "windows": len(windows_df), "weather": len(weather_df)},
        "anomaly_rate_by_split": (
            windows_df.groupby("split")["is_anomaly"].mean().round(4).to_dict() if len(windows_df) else {}
        ),
        "content_hash": {
            "tasks": _df_hash(tasks_df), "windows": _df_hash(windows_df), "weather": _df_hash(weather_df),
        },
        "generation_seconds": round(time.time() - t0, 2),
    }
    with open(f"{out_dir}/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    return manifest


def _window_base_row(machine, weather_ctx, operator, task_id, task_type, day, split) -> dict:
    return {
        "machine_id": machine.machine_id, "site_id": machine.site_id,
        "machine_type": machine.type, "machine_age_years": machine.age_years,
        "operator_id": operator.operator_id, "operator_skill": operator.skill,
        "task_id": task_id or "", "task_type": task_type or "",
        "weather": weather_ctx["weather"], "rainfall_mm_h": weather_ctx["rainfall_mm_h"],
        "visibility_m": weather_ctx["visibility_m"], "wind_kmh": weather_ctx["wind_kmh"],
        "ambient_temp_c": weather_ctx["ambient_temp_c"], "ground": weather_ctx["ground"],
        "day": day, "split": split,
    }


def _write_gz_csv(df: pd.DataFrame, path: str) -> None:
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        df.to_csv(gz, index=False)


def _df_hash(df: pd.DataFrame) -> str:
    if len(df) == 0:
        return hashlib.sha256(b"empty").hexdigest()[:16]
    h = pd.util.hash_pandas_object(df, index=False).sum()
    return hashlib.sha256(str(int(h)).encode()).hexdigest()[:16]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--profile", choices=list(PROFILES.keys()), default="dev")
    parser.add_argument("--out", default="ml/data")
    args = parser.parse_args()

    import os
    os.makedirs(args.out, exist_ok=True)
    manifest = generate(args.profile, args.seed, args.out)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
