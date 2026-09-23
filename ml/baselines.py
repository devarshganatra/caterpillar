"""
Hierarchical idle-ratio baselines for operator-deviation detection (ARCH
section 6.6). Computed once offline from the train split; loaded by
backend/app/services/attribution.py at serving time via the ModelRegistry.

Context = (task_type, weather, operator_skill). Levels, in backoff order
(most specific first): operator x context -> skill x context -> task_type ->
global. Each entry stores {n, ewma, median, mad} of the OPERATOR-attributed
idle ratio (idle_s_OPERATOR / window_s) for windows with idle_seconds > 0.

Usage:
    PYTHONPATH=. venv/bin/python -m ml.baselines --data ml/data --out ml/artifacts
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

KEY_SEP = "||"


def _stats(ratios: pd.Series) -> dict:
    ratios = ratios.sort_index()  # sorted by original row order (== day/time order, generator writes in time order)
    n = int(len(ratios))
    median = float(ratios.median())
    mad = float((ratios - median).abs().median())
    ewma = float(ratios.ewm(alpha=0.1, adjust=False).mean().iloc[-1]) if n > 0 else 0.0
    return {"n": n, "ewma": round(ewma, 4), "median": round(median, 4), "mad": round(mad, 4)}


def build_baselines(data_dir: str) -> dict:
    df = pd.read_csv(f"{data_dir}/windows.csv.gz")
    df = df[df["split"] == "train"].copy()
    df = df[df["idle_s_PLANNED"].notna()]  # sanity: idle_s_* columns must exist
    df = df[(df["idle_s_PLANNED"] + df["idle_s_MACHINE"] + df["idle_s_WEATHER"]
             + df["idle_s_SITE"] + df["idle_s_OPERATOR"]) > 0]  # idle windows only

    df["operator_idle_ratio"] = df["idle_s_OPERATOR"] / df["window_s"]
    df = df.sort_values(["day"]).reset_index(drop=True)

    # Idle-gap windows are generated with task_type="" (no task in progress),
    # which pandas.read_csv reads back as NaN — and groupby DROPS any row
    # with NaN in a groupby key column by default, silently discarding every
    # idle window (all of them, since idle windows are exactly what this
    # file computes baselines over). Give it a real sentinel value instead.
    df["task_type"] = df["task_type"].fillna("NONE")

    levels: dict[str, dict[str, dict]] = {
        "operator_context": {}, "skill_context": {}, "task_type": {}, "global": {},
    }

    for key, sub in df.groupby(["operator_id", "task_type", "weather", "operator_skill"]):
        levels["operator_context"][KEY_SEP.join(map(str, key))] = _stats(sub["operator_idle_ratio"])

    for key, sub in df.groupby(["operator_skill", "task_type", "weather"]):
        levels["skill_context"][KEY_SEP.join(map(str, key))] = _stats(sub["operator_idle_ratio"])

    for key, sub in df.groupby(["task_type"]):
        key = key if isinstance(key, str) else key[0]
        levels["task_type"][str(key)] = _stats(sub["operator_idle_ratio"])

    levels["global"]["global"] = _stats(df["operator_idle_ratio"])

    return {
        "levels": levels,
        "n_windows": int(len(df)),
        "key_separator": KEY_SEP,
        "key_order": {
            "operator_context": ["operator_id", "task_type", "weather", "operator_skill"],
            "skill_context": ["operator_skill", "task_type", "weather"],
            "task_type": ["task_type"],
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="ml/data")
    parser.add_argument("--out", default="ml/artifacts")
    args = parser.parse_args()

    baselines = build_baselines(args.data)
    with open(f"{args.out}/idle_baselines.json", "w") as f:
        json.dump(baselines, f, indent=2)
    print(json.dumps({"n_windows": baselines["n_windows"],
                       "levels": {k: len(v) for k, v in baselines["levels"].items()}}, indent=2))


if __name__ == "__main__":
    main()
