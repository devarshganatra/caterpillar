"""
Lazy-loaded singleton registry for ML model artifacts (ml/artifacts/*).

Every model-consuming service (eta.py, and attribution.py/anomaly.py in
Batch 3C) goes through this registry rather than loading files itself, so:
  - artifacts are loaded once per process, not once per prediction
  - a missing/incompatible artifact degrades to an explicit UNAVAILABLE
    status instead of raising and taking down the warm worker
  - library-version mismatches (e.g. artifacts trained on a different
    scikit-learn/xgboost than what's installed) are caught explicitly
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backend.app.config import settings

logger = logging.getLogger(__name__)

Status = str  # "READY" | "MISSING" | "VERSION_MISMATCH" | "LOAD_ERROR"


def _installed_lib_versions() -> dict[str, str]:
    versions = {}
    for mod_name in ("xgboost", "sklearn", "shap", "numpy", "pandas"):
        try:
            mod = __import__(mod_name)
            versions[mod_name] = getattr(mod, "__version__", "unknown")
        except ImportError:
            versions[mod_name] = "not_installed"
    return versions


def _major_minor(v: str) -> str:
    parts = v.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else v


@dataclass
class ModelRegistry:
    artifacts_dir: str
    eta_status: Status = "MISSING"
    eta_p50_model: Any = None
    eta_q_model: Any = None
    eta_background: dict[str, np.ndarray] = field(default_factory=dict)
    eta_meta: dict | None = None

    anomaly_status: Status = "MISSING"
    iforest_model: Any = None
    iforest_meta: dict | None = None
    window_stats: dict | None = None

    baselines_status: Status = "MISSING"
    idle_baselines: dict | None = None

    _loaded: bool = False

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._load_eta()
        self._load_anomaly()
        self._load_baselines()

    def _load_anomaly(self) -> None:
        meta_path = os.path.join(self.artifacts_dir, "iforest_meta.json")
        model_path = os.path.join(self.artifacts_dir, "iforest.joblib")
        stats_path = os.path.join(self.artifacts_dir, "window_stats.json")

        # window_stats.json (robust-z fallback) is independent of the IF
        # model itself — load it even if the model is missing/broken.
        if os.path.exists(stats_path):
            try:
                with open(stats_path) as f:
                    self.window_stats = json.load(f)
            except Exception as e:
                logger.error(f"Failed to read window_stats.json: {e}")

        if not (os.path.exists(meta_path) and os.path.exists(model_path)):
            self.anomaly_status = "MISSING"
            return

        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read iforest_meta.json: {e}")
            self.anomaly_status = "LOAD_ERROR"
            return

        installed = _installed_lib_versions()
        trained = meta.get("lib_versions", {})
        trained_sklearn = trained.get("sklearn")
        if trained_sklearn and _major_minor(trained_sklearn) != _major_minor(installed.get("sklearn", "")):
            logger.warning(
                f"iforest trained with sklearn=={trained_sklearn}, installed sklearn=={installed.get('sklearn')}"
            )
            self.anomaly_status = "VERSION_MISMATCH"
            self.iforest_meta = meta
            return

        try:
            import joblib
            self.iforest_model = joblib.load(model_path)
            self.iforest_meta = meta
            self.anomaly_status = "READY"
        except Exception as e:
            logger.error(f"Failed to load iforest.joblib: {e}")
            self.anomaly_status = "LOAD_ERROR"

    def _load_baselines(self) -> None:
        path = os.path.join(self.artifacts_dir, "idle_baselines.json")
        if not os.path.exists(path):
            self.baselines_status = "MISSING"
            return
        try:
            with open(path) as f:
                self.idle_baselines = json.load(f)
            self.baselines_status = "READY"
        except Exception as e:
            logger.error(f"Failed to load idle_baselines.json: {e}")
            self.baselines_status = "LOAD_ERROR"

    def _load_eta(self) -> None:
        meta_path = os.path.join(self.artifacts_dir, "eta_meta.json")
        p50_path = os.path.join(self.artifacts_dir, "eta_p50.json")
        q_path = os.path.join(self.artifacts_dir, "eta_q.json")
        bg_path = os.path.join(self.artifacts_dir, "eta_background.npz")

        if not (os.path.exists(meta_path) and os.path.exists(p50_path) and os.path.exists(q_path)):
            self.eta_status = "MISSING"
            return

        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read eta_meta.json: {e}")
            self.eta_status = "LOAD_ERROR"
            return

        installed = _installed_lib_versions()
        trained = meta.get("lib_versions", {})
        for lib in ("xgboost", "sklearn"):
            trained_v = trained.get(lib)
            if trained_v and _major_minor(trained_v) != _major_minor(installed.get(lib, "")):
                logger.warning(
                    f"eta model trained with {lib}=={trained_v}, installed {lib}=={installed.get(lib)}"
                )
                self.eta_status = "VERSION_MISMATCH"
                self.eta_meta = meta
                return

        try:
            import xgboost as xgb

            p50_model = xgb.XGBRegressor()
            p50_model.load_model(p50_path)
            q_model = xgb.XGBRegressor()
            q_model.load_model(q_path)

            background: dict[str, np.ndarray] = {}
            if os.path.exists(bg_path):
                npz = np.load(bg_path)
                for key in npz.files:
                    background[key] = npz[key]

            self.eta_p50_model = p50_model
            self.eta_q_model = q_model
            self.eta_background = background
            self.eta_meta = meta
            self.eta_status = "READY"
        except Exception as e:
            logger.error(f"Failed to load ETA model artifacts: {e}")
            self.eta_status = "LOAD_ERROR"

    def status(self) -> dict:
        return {
            "eta_status": self.eta_status,
            "anomaly_status": self.anomaly_status,
            "baselines_status": self.baselines_status,
            "eta_model_version": (self.eta_meta or {}).get("model_version"),
        }


_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry(artifacts_dir=settings.ml_artifacts_dir)
    _registry.load()
    return _registry


def reset_registry_for_tests() -> None:
    """Test-only helper: forces the next get_registry() call to reload from disk."""
    global _registry
    _registry = None
