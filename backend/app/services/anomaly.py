"""
Anomaly detection on window aggregates (ARCH section 6.7). Primary: Isolation
Forest with percentile-calibrated score + SHAP top-3 drivers. Fallback:
robust-z when the model is unavailable or undertrained. Never modifies
safety state — output is an AnomalyResult; the warm worker (Batch 3D) turns
`is_anomalous` into an OPERATIONAL_ANOMALY Event for the correlator.
"""
from __future__ import annotations

import logging

import numpy as np
import shap

from backend.app.services.ml_registry import ModelRegistry, get_registry
from contracts.intelligence import AnomalyDriver, AnomalyResult
from ml.constants import WINDOW_FEATURES
from ml.features import robust_z

logger = logging.getLogger(__name__)

_iforest_explainer_cache: dict[str, "shap.TreeExplainer"] = {}


def _is_eligible(features: dict) -> bool:
    working_ratio = features.get("working_ratio")
    cycle_count = features.get("cycle_count")
    if working_ratio is None or working_ratio < 0.5:
        return False
    if cycle_count is None or cycle_count < 2:
        return False
    for f in WINDOW_FEATURES:
        if features.get(f) is None:
            return False
    return True


def _feature_vector(features: dict) -> np.ndarray:
    return np.array([float(features[f]) for f in WINDOW_FEATURES], dtype=float)


def _get_iforest_explainer(registry: ModelRegistry):
    version = (registry.iforest_meta or {}).get("model_version")
    if version not in _iforest_explainer_cache:
        _iforest_explainer_cache.clear()
        _iforest_explainer_cache[version] = shap.TreeExplainer(registry.iforest_model)
    return _iforest_explainer_cache[version]


def _calibrate(raw_score: float, quantiles: list[float]) -> float:
    """percentile(s) = searchsorted(q, s) / 1000, clamped to [0, 1]."""
    idx = int(np.searchsorted(quantiles, raw_score))
    return max(0.0, min(1.0, idx / 1000.0))


def score_window(window_id: str, machine_id: str, features: dict, registry: ModelRegistry | None = None) -> AnomalyResult:
    registry = registry or get_registry()

    if not _is_eligible(features):
        return AnomalyResult(
            window_id=window_id, machine_id=machine_id, method="SKIPPED",
            reason="NOT_WORKING_WINDOW", is_anomalous=False,
        )

    x = _feature_vector(features)

    if registry.anomaly_status == "READY" and (registry.iforest_meta or {}).get("n_train", 0) >= 1:
        try:
            raw_score = float(-registry.iforest_model.score_samples(x.reshape(1, -1))[0])
            quantiles = registry.iforest_meta["score_quantiles"]
            score = _calibrate(raw_score, quantiles)
            threshold_pct = registry.iforest_meta.get("threshold_pct", 0.97)
            is_anomalous = score >= threshold_pct

            drivers, drivers_method = _shap_drivers(x, registry)
            return AnomalyResult(
                window_id=window_id, machine_id=machine_id, method="IFOREST",
                score=round(score, 4), threshold=threshold_pct, is_anomalous=is_anomalous,
                drivers=drivers, drivers_method=drivers_method,
                model_version=registry.iforest_meta.get("model_version"),
            )
        except Exception as e:
            logger.error(f"IsolationForest scoring failed for window {window_id}: {e}, falling back to robust-z")

    if registry.window_stats:
        return _robust_z_fallback(window_id, machine_id, features, registry.window_stats)

    return AnomalyResult(window_id=window_id, machine_id=machine_id, method="UNAVAILABLE", is_anomalous=False)


def _shap_drivers(x: np.ndarray, registry: ModelRegistry) -> tuple[list[AnomalyDriver], str]:
    stats = registry.window_stats or {}
    try:
        explainer = _get_iforest_explainer(registry)
        shap_values = np.asarray(explainer.shap_values(x.reshape(1, -1)))[0]
        order = np.argsort(-np.abs(shap_values))[:3]
        drivers = []
        for i in order:
            feat = WINDOW_FEATURES[i]
            s = stats.get(feat, {"median": 0.0, "mad": 0.0})
            drivers.append(AnomalyDriver(
                feature=feat, value=round(float(x[i]), 4),
                robust_z=round(robust_z(float(x[i]), s["median"], s["mad"]), 3),
                shap=round(float(shap_values[i]), 4),
            ))
        return drivers, "SHAP"
    except Exception as e:
        logger.warning(f"SHAP driver computation failed, falling back to robust-z drivers: {e}")
        if not stats:
            return [], "NONE"
        zs = []
        for i, feat in enumerate(WINDOW_FEATURES):
            s = stats.get(feat, {"median": 0.0, "mad": 0.0})
            zs.append((feat, float(x[i]), robust_z(float(x[i]), s["median"], s["mad"])))
        zs.sort(key=lambda t: -abs(t[2]))
        drivers = [AnomalyDriver(feature=f, value=round(v, 4), robust_z=round(z, 3), shap=None) for f, v, z in zs[:3]]
        return drivers, "ROBUST_Z"


def _robust_z_fallback(window_id: str, machine_id: str, features: dict, window_stats: dict) -> AnomalyResult:
    zs = []
    for f in WINDOW_FEATURES:
        s = window_stats.get(f, {"median": 0.0, "mad": 0.0})
        zs.append((f, float(features[f]), robust_z(float(features[f]), s["median"], s["mad"])))

    max_abs_z = max(abs(z) for _, _, z in zs) if zs else 0.0
    zs.sort(key=lambda t: -abs(t[2]))
    drivers = [AnomalyDriver(feature=f, value=round(v, 4), robust_z=round(z, 3), shap=None) for f, v, z in zs[:3]]

    from backend.app.config import settings
    threshold = settings.robust_z_threshold
    return AnomalyResult(
        window_id=window_id, machine_id=machine_id, method="ROBUST_Z",
        score=round(max_abs_z, 3), threshold=threshold, is_anomalous=max_abs_z > threshold,
        drivers=drivers, drivers_method="ROBUST_Z",
    )
