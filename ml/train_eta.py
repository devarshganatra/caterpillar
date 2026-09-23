"""
Trains the ETA models: a P50 point-estimate regressor plus a joint P10/P90
quantile regressor, saves them as portable XGBoost JSON, and writes a
per-task-type SHAP background sample + metadata.

Usage:
    PYTHONPATH=. venv/bin/python -m ml.train_eta --data ml/data --out ml/artifacts --seed 42
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time

import numpy as np
import pandas as pd
import xgboost as xgb
import sklearn

from ml.constants import TASK_TYPE_NAMES, ETA_FACTOR_GROUPS, ETA_CATEGORICAL
from ml.features import eta_feature_columns, encode_eta_row


def _lib_versions() -> dict[str, str]:
    return {"xgboost": xgb.__version__, "sklearn": sklearn.__version__, "numpy": np.__version__}


def _rows_to_matrix(df: pd.DataFrame, feature_columns: list[str]) -> np.ndarray:
    rows = df.to_dict("records")
    return np.vstack([encode_eta_row(r, feature_columns) for r in rows])


def _df_content_hash(df: pd.DataFrame) -> str:
    h = pd.util.hash_pandas_object(df, index=False).sum()
    return hashlib.sha256(str(int(h)).encode()).hexdigest()[:8]


def train(data_dir: str, out_dir: str, seed: int) -> dict:
    t0 = time.time()
    tasks = pd.read_csv(f"{data_dir}/tasks.csv.gz")

    # Rename to the eta_feature_row contract's expected field names
    tasks = tasks.rename(columns={"operator_skill": "operator_skill"})
    tasks["hauler_queue_mean"] = tasks.get("hauler_queue_mean", 1.0)

    feature_columns = eta_feature_columns()

    train_df = tasks[tasks["split"] == "train"].reset_index(drop=True)
    val_df = tasks[tasks["split"] == "val"].reset_index(drop=True)
    if len(val_df) == 0:
        # Small profiles (e.g. "tiny") can produce an empty val split; fall
        # back to a slice of train so training still runs end-to-end.
        val_df = train_df.tail(max(5, len(train_df) // 10)).reset_index(drop=True)

    X_train = _rows_to_matrix(train_df, feature_columns)
    y_train = train_df["actual_min"].values
    X_val = _rows_to_matrix(val_df, feature_columns)
    y_val = val_df["actual_min"].values

    p50_model = xgb.XGBRegressor(
        objective="reg:absoluteerror", tree_method="hist", n_estimators=600,
        learning_rate=0.05, max_depth=6, early_stopping_rounds=50,
        n_jobs=4, random_state=seed, enable_categorical=False,
    )
    p50_model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

    q_model = xgb.XGBRegressor(
        objective="reg:quantileerror", quantile_alpha=np.array([0.1, 0.9]),
        tree_method="hist", n_estimators=600, learning_rate=0.05, max_depth=6,
        early_stopping_rounds=50, n_jobs=4, random_state=seed, enable_categorical=False,
    )
    q_model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

    val_pred = p50_model.predict(X_val)
    val_mae_p50 = float(np.mean(np.abs(val_pred - y_val)))

    import os
    os.makedirs(out_dir, exist_ok=True)
    p50_model.save_model(f"{out_dir}/eta_p50.json")
    q_model.save_model(f"{out_dir}/eta_q.json")

    # Per-task-type SHAP background: up to 100 deterministic-sampled train rows.
    rng = np.random.default_rng(seed)
    background: dict[str, np.ndarray] = {}
    for task_type in TASK_TYPE_NAMES:
        sub = train_df[train_df["task_type"] == task_type]
        if len(sub) == 0:
            continue
        n = min(100, len(sub))
        idx = rng.choice(len(sub), size=n, replace=False)
        background[task_type] = _rows_to_matrix(sub.iloc[idx], feature_columns)
    np.savez(f"{out_dir}/eta_background.npz", **background)

    train_hash = _df_content_hash(train_df)
    model_version = f"eta-{time.strftime('%Y%m%d')}-s{seed}-{train_hash}"

    meta = {
        "model_version": model_version,
        "feature_columns": feature_columns,
        "categorical_levels": dict(ETA_CATEGORICAL),
        "factor_groups": ETA_FACTOR_GROUPS,
        "target_unit": "sim_minutes",
        "n_train": len(train_df),
        "n_val": len(val_df),
        "val_mae_p50": val_mae_p50,
        "lib_versions": _lib_versions(),
        "training_seconds": round(time.time() - t0, 2),
    }
    with open(f"{out_dir}/eta_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    return meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="ml/data")
    parser.add_argument("--out", default="ml/artifacts")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    meta = train(args.data, args.out, args.seed)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
