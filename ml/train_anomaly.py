"""
Trains the Isolation Forest anomaly detector on window aggregates, with
percentile calibration for a bounded [0,1] score and robust per-feature
stats for the robust-z fallback (used when the model is unavailable).

Usage:
    PYTHONPATH=. venv/bin/python -m ml.train_anomaly --data ml/data --out ml/artifacts --seed 42
"""
from __future__ import annotations

import argparse
import json
import time

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest

from ml.constants import WINDOW_FEATURES


def _eligible(df: pd.DataFrame) -> pd.DataFrame:
    """Same eligibility rule as serving (backend/app/services/anomaly.py):
    working_ratio >= 0.5 and cycle_count >= 2 and no missing features."""
    mask = (df["working_ratio"] >= 0.5) & (df["cycle_count"] >= 2)
    for f in WINDOW_FEATURES:
        mask &= df[f].notna()
    return df[mask].copy()


def train(data_dir: str, out_dir: str, seed: int) -> dict:
    t0 = time.time()
    windows = pd.read_csv(f"{data_dir}/windows.csv.gz")
    train_df = _eligible(windows[windows["split"] == "train"])

    X_train = train_df[WINDOW_FEATURES].values.astype(float)

    model = IsolationForest(n_estimators=200, contamination=0.03, random_state=seed, n_jobs=4)
    model.fit(X_train)

    raw_scores = -model.score_samples(X_train)  # higher = more anomalous
    quantiles = np.quantile(raw_scores, np.linspace(0, 1, 1001))

    # Robust per-feature stats (median/MAD) for the robust-z fallback.
    window_stats = {}
    for f in WINDOW_FEATURES:
        vals = train_df[f].values.astype(float)
        median = float(np.median(vals))
        mad = float(np.median(np.abs(vals - median)))
        window_stats[f] = {"median": median, "mad": mad}

    import os
    os.makedirs(out_dir, exist_ok=True)
    joblib.dump(model, f"{out_dir}/iforest.joblib")

    model_version = f"iforest-{time.strftime('%Y%m%d')}-s{seed}-n{len(train_df)}"
    meta = {
        "model_version": model_version,
        "features": WINDOW_FEATURES,
        "score_quantiles": quantiles.tolist(),
        "threshold_pct": 0.97,
        "n_train": int(len(train_df)),
        "lib_versions": {"sklearn": sklearn.__version__, "numpy": np.__version__, "shap": __import__("shap").__version__},
        "training_seconds": round(time.time() - t0, 2),
    }
    with open(f"{out_dir}/iforest_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    with open(f"{out_dir}/window_stats.json", "w") as f:
        json.dump(window_stats, f, indent=2)

    return meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="ml/data")
    parser.add_argument("--out", default="ml/artifacts")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    meta = train(args.data, args.out, args.seed)
    # score_quantiles is long; keep console output readable
    print(json.dumps({k: v for k, v in meta.items() if k != "score_quantiles"}, indent=2))


if __name__ == "__main__":
    main()
