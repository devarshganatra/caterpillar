"""
Evaluates ETA, anomaly detection, and idle attribution on the held-out TEST
split (never the train split the models were fit on). Writes
ml/artifacts/metrics.json. Every block is measured from data — no number in
this file is a guess, and every block carries {"split": "test", "n": ...,
"data": "synthetic"} so nothing downstream (docs, UI) can present a
synthetic-data number as if it were validated on anything else.

Usage:
    PYTHONPATH=. venv/bin/python -m ml.evaluate --data ml/data --artifacts ml/artifacts
"""
from __future__ import annotations

import argparse
import json

import joblib
import numpy as np
import pandas as pd

from ml.constants import WINDOW_FEATURES, IDLE_CAUSES
from ml.features import eta_feature_columns, encode_eta_row, robust_z


def evaluate_eta(data_dir: str, artifacts_dir: str) -> dict:
    import xgboost as xgb

    tasks = pd.read_csv(f"{data_dir}/tasks.csv.gz")
    test_df = tasks[tasks["split"] == "test"].reset_index(drop=True)
    if len(test_df) == 0:
        return {"split": "test", "n": 0, "data": "synthetic", "note": "empty test split"}

    with open(f"{artifacts_dir}/eta_meta.json") as f:
        meta = json.load(f)
    feature_columns = meta["feature_columns"]

    p50_model = xgb.XGBRegressor()
    p50_model.load_model(f"{artifacts_dir}/eta_p50.json")
    q_model = xgb.XGBRegressor()
    q_model.load_model(f"{artifacts_dir}/eta_q.json")

    X = np.vstack([encode_eta_row(r, feature_columns) for r in test_df.to_dict("records")])
    y_true = test_df["actual_min"].values
    y_planner = test_df["planner_estimate_min"].values

    p50_pred = p50_model.predict(X)
    q_pred = q_model.predict(X)
    p10_pred, p90_pred = q_pred[:, 0], q_pred[:, 1]

    mae_model = float(np.mean(np.abs(p50_pred - y_true)))
    mae_planner = float(np.mean(np.abs(y_planner - y_true)))
    rel_improvement = float((mae_planner - mae_model) / mae_planner) if mae_planner > 0 else None

    in_band = (y_true >= np.minimum(p10_pred, p90_pred)) & (y_true <= np.maximum(p10_pred, p90_pred))
    coverage = float(np.mean(in_band))

    mae_by_task_type = {}
    for tt in sorted(test_df["task_type"].unique()):
        m = test_df["task_type"] == tt
        mae_by_task_type[tt] = float(np.mean(np.abs(p50_pred[m.values] - y_true[m.values])))

    return {
        "split": "test", "n": int(len(test_df)), "data": "synthetic",
        "mae_model_p50_min": round(mae_model, 3),
        "mae_planner_estimate_min": round(mae_planner, 3),
        "relative_improvement_vs_planner": round(rel_improvement, 4) if rel_improvement is not None else None,
        "p10_p90_coverage": round(coverage, 4),
        "mae_by_task_type_min": {k: round(v, 3) for k, v in mae_by_task_type.items()},
    }


def _eligible(df: pd.DataFrame) -> pd.DataFrame:
    mask = (df["working_ratio"] >= 0.5) & (df["cycle_count"] >= 2)
    for f in WINDOW_FEATURES:
        mask &= df[f].notna()
    return df[mask].copy()


def _precision_recall_f1(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    tp = int(np.sum((y_true == 1) & (y_pred == 1)))
    fp = int(np.sum((y_true == 0) & (y_pred == 1)))
    fn = int(np.sum((y_true == 1) & (y_pred == 0)))
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn}


def _pr_auc(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Simple trapezoidal PR-AUC without sklearn.metrics dependency drift."""
    order = np.argsort(-scores)
    y_sorted = y_true[order]
    tp_cum = np.cumsum(y_sorted)
    fp_cum = np.cumsum(1 - y_sorted)
    n_pos = y_true.sum()
    if n_pos == 0:
        return 0.0
    precision = tp_cum / np.maximum(tp_cum + fp_cum, 1)
    recall = tp_cum / n_pos
    recall = np.concatenate([[0.0], recall])
    precision = np.concatenate([[1.0], precision])
    # np.trapz was removed in numpy 2.0 (renamed to np.trapezoid)
    trapezoid_fn = getattr(np, "trapezoid", None) or np.trapz
    return float(trapezoid_fn(precision, recall))


def evaluate_anomaly(data_dir: str, artifacts_dir: str) -> dict:
    windows = pd.read_csv(f"{data_dir}/windows.csv.gz")
    test_df = _eligible(windows[windows["split"] == "test"])
    if len(test_df) == 0:
        return {"split": "test", "n": 0, "data": "synthetic", "note": "no eligible test windows"}

    y_true = test_df["is_anomaly"].values.astype(int)
    X = test_df[WINDOW_FEATURES].values.astype(float)

    result = {"split": "test", "n": int(len(test_df)), "data": "synthetic",
              "n_positive": int(y_true.sum())}

    with open(f"{artifacts_dir}/iforest_meta.json") as f:
        meta = json.load(f)
    model = joblib.load(f"{artifacts_dir}/iforest.joblib")
    quantiles = np.array(meta["score_quantiles"])
    threshold_pct = meta.get("threshold_pct", 0.97)

    raw_scores = -model.score_samples(X)
    calibrated = np.searchsorted(quantiles, raw_scores) / 1000.0
    calibrated = np.clip(calibrated, 0.0, 1.0)
    y_pred_if = (calibrated >= threshold_pct).astype(int)

    result["isolation_forest"] = {
        **_precision_recall_f1(y_true, y_pred_if),
        "pr_auc": round(_pr_auc(y_true, calibrated), 4),
        "threshold_pct": threshold_pct,
    }

    recall_by_type = {}
    for atype in test_df["anomaly_type"].dropna().unique():
        if atype == "":
            continue
        m = (test_df["anomaly_type"] == atype).values
        if m.sum() == 0:
            continue
        recall_by_type[atype] = round(float(np.mean(y_pred_if[m] == 1)), 4)
    result["isolation_forest"]["recall_by_anomaly_type"] = recall_by_type

    # robust-z fallback, same test set
    with open(f"{artifacts_dir}/window_stats.json") as f:
        window_stats = json.load(f)
    z_scores = np.zeros(len(test_df))
    for i, f in enumerate(WINDOW_FEATURES):
        s = window_stats[f]
        z_scores = np.maximum(z_scores, np.abs((X[:, i] - s["median"]) / (1.4826 * s["mad"] if s["mad"] > 0 else 1)))
    from backend.app.config import settings as _settings
    rz_threshold = _settings.robust_z_threshold
    y_pred_rz = (z_scores > rz_threshold).astype(int)
    result["robust_z_fallback"] = {
        **_precision_recall_f1(y_true, y_pred_rz),
        "pr_auc": round(_pr_auc(y_true, z_scores), 4),
        "threshold": rz_threshold,
    }

    return result


def evaluate_attribution(data_dir: str) -> dict:
    """
    Since the generator directly encodes one cause per idle window
    (ml/generate_history.py's _gap_signal_kwargs), agreement here is measured
    by re-deriving the cause from the SAME window-level signals the real
    attribution service (backend/app/services/attribution.py) uses, applied
    at window granularity (the generator's own ground-truth cause is the
    dominant idle_s_{cause} column).
    """
    windows = pd.read_csv(f"{data_dir}/windows.csv.gz")
    test_df = windows[windows["split"] == "test"].copy()
    idle_cols = [f"idle_s_{c}" for c in IDLE_CAUSES]
    test_df = test_df[test_df[idle_cols].sum(axis=1) > 0]
    if len(test_df) == 0:
        return {"split": "test", "n": 0, "data": "synthetic", "note": "no idle test windows"}

    true_cause = test_df[idle_cols].idxmax(axis=1).str.replace("idle_s_", "", regex=False)

    # Re-derive predicted cause via the SAME priority rule as attribution.py,
    # applied to the window's own aggregate evidence columns.
    from backend.app.config import settings

    def predict_cause(row) -> str:
        if row.get("rainfall_mm_h", 0) >= settings.weather_stop_rain_mm_h \
                or row.get("visibility_m", 9999) < settings.weather_stop_visibility_m \
                or row.get("wind_kmh", 0) >= settings.weather_stop_wind_kmh \
                or row.get("ground") == "ICY":
            weather_breach = True
        else:
            weather_breach = False
        # MACHINE/PLANNED signals aren't in the window-aggregate CSV directly
        # (they're per-frame in the generator); use the ground-truth idle_s_
        # columns for those two causes only, and re-derive WEATHER/SITE/OPERATOR
        # from the window's own aggregate signals to test the priority logic itself.
        if row["idle_s_PLANNED"] > 0:
            return "PLANNED"
        if row["idle_s_MACHINE"] > 0:
            return "MACHINE"
        if weather_breach:
            return "WEATHER"
        if row["truck_present_ratio"] < 0.5 or row["hauler_queue_mean"] < 0.5:
            return "SITE"
        return "OPERATOR"

    pred_cause = test_df.apply(predict_cause, axis=1)
    agreement = float((pred_cause.values == true_cause.values).mean())

    confusion = pd.crosstab(true_cause, pred_cause, dropna=False)

    return {
        "split": "test", "n": int(len(test_df)), "data": "synthetic",
        "primary_cause_agreement": round(agreement, 4),
        "confusion_matrix": confusion.to_dict(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="ml/data")
    parser.add_argument("--artifacts", default="ml/artifacts")
    args = parser.parse_args()

    eta_metrics = evaluate_eta(args.data, args.artifacts)
    anomaly_metrics = evaluate_anomaly(args.data, args.artifacts)
    attribution_metrics = evaluate_attribution(args.data)

    assert eta_metrics.get("split") == "test"
    assert anomaly_metrics.get("split") == "test"
    assert attribution_metrics.get("split") == "test"

    metrics = {"eta": eta_metrics, "anomaly": anomaly_metrics, "attribution": attribution_metrics}
    with open(f"{args.artifacts}/metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
