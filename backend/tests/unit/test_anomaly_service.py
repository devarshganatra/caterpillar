"""Unit tests for backend.app.services.anomaly: IF scoring, calibration, robust-z fallback, eligibility."""
import pytest

from backend.app.services import anomaly
from backend.app.services.ml_registry import ModelRegistry
from ml.constants import WINDOW_FEATURES

pytestmark = pytest.mark.unit


def _features(**overrides):
    base = {
        "fuel_per_cycle": 0.4, "rpm_mean": 1900.0, "rpm_std": 65.0, "hyd_p95": 260.0,
        "idle_ratio": 0.0, "cycle_time_mean": 65.0, "cycle_time_cv": 0.1, "temp_slope": 0.1,
        "working_ratio": 0.9, "cycle_count": 5,
    }
    base.update(overrides)
    return base


@pytest.fixture(scope="module")
def registry():
    reg = ModelRegistry(artifacts_dir="ml/artifacts")
    reg.load()
    if reg.anomaly_status != "READY":
        pytest.skip("ml/artifacts/iforest.joblib not present — run `make train` first")
    return reg


def test_ineligible_window_skipped_low_working_ratio(registry):
    result = anomaly.score_window("w1", "EXC001", _features(working_ratio=0.2), registry)
    assert result.method == "SKIPPED"
    assert result.reason == "NOT_WORKING_WINDOW"
    assert result.is_anomalous is False


def test_ineligible_window_skipped_too_few_cycles(registry):
    result = anomaly.score_window("w1", "EXC001", _features(cycle_count=1), registry)
    assert result.method == "SKIPPED"


def test_ineligible_window_skipped_missing_feature(registry):
    feats = _features()
    del feats["fuel_per_cycle"]
    result = anomaly.score_window("w1", "EXC001", feats, registry)
    assert result.method == "SKIPPED"


def test_eligible_window_scored_by_iforest(registry):
    result = anomaly.score_window("w1", "EXC001", _features(), registry)
    assert result.method == "IFOREST"
    assert 0.0 <= result.score <= 1.0
    assert result.model_version is not None


def test_high_fuel_anomaly_has_fuel_in_top_drivers(registry):
    """An extreme fuel_per_cycle should surface fuel_per_cycle among the top-3 SHAP drivers."""
    result = anomaly.score_window("w1", "EXC001", _features(fuel_per_cycle=5.0), registry)
    driver_features = [d.feature for d in result.drivers]
    assert "fuel_per_cycle" in driver_features


def test_score_monotonic_with_more_extreme_features(registry):
    normal = anomaly.score_window("w1", "EXC001", _features(), registry)
    extreme = anomaly.score_window("w1", "EXC001", _features(fuel_per_cycle=10.0, rpm_std=500.0), registry)
    assert extreme.score >= normal.score


def test_missing_iforest_falls_back_to_robust_z(tmp_path):
    import json
    (tmp_path / "window_stats.json").write_text(json.dumps({
        f: {"median": 0.4 if f == "fuel_per_cycle" else 1.0, "mad": 0.1} for f in WINDOW_FEATURES
    }))
    reg = ModelRegistry(artifacts_dir=str(tmp_path))
    reg.load()
    assert reg.anomaly_status == "MISSING"
    assert reg.window_stats is not None

    result = anomaly.score_window("w1", "EXC001", _features(fuel_per_cycle=10.0), reg)
    assert result.method == "ROBUST_Z"
    assert result.is_anomalous is True  # fuel_per_cycle=10 is a huge z-score vs median 0.4, mad 0.1


def test_missing_everything_gives_unavailable(tmp_path):
    reg = ModelRegistry(artifacts_dir=str(tmp_path))  # empty dir, no window_stats.json either
    reg.load()
    result = anomaly.score_window("w1", "EXC001", _features(), reg)
    assert result.method == "UNAVAILABLE"
    assert result.is_anomalous is False


def test_robust_z_fallback_normal_not_flagged(tmp_path):
    import json
    # medians/mads centered on _features()'s own default values, so a window
    # close to those defaults is genuinely "normal" relative to this baseline.
    defaults = _features()
    (tmp_path / "window_stats.json").write_text(json.dumps({
        f: {"median": defaults[f], "mad": max(abs(defaults[f]) * 0.1, 0.05)} for f in WINDOW_FEATURES
    }))
    reg = ModelRegistry(artifacts_dir=str(tmp_path))
    reg.load()
    result = anomaly.score_window("w1", "EXC001", _features(fuel_per_cycle=0.42), reg)  # close to median
    assert result.is_anomalous is False
