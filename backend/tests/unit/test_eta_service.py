"""Unit tests for backend.app.services.eta: prediction, SHAP explanation, blending, slip."""
import numpy as np
import pytest

from backend.app.services import eta
from backend.app.services.ml_registry import ModelRegistry
from ml.features import eta_feature_columns, encode_eta_row, eta_feature_row

pytestmark = pytest.mark.unit


# --- predict_task / explain (using the real trained ml/artifacts) ---------

@pytest.fixture(scope="module")
def registry():
    reg = ModelRegistry(artifacts_dir="ml/artifacts")
    reg.load()
    if reg.eta_status != "READY":
        pytest.skip("ml/artifacts/eta_*.json not present — run `make train-eta` first")
    return reg


def _ctx(**overrides):
    base = dict(
        task_type="TRUCK_LOADING", machine_type="EXCAVATOR", machine_age_years=2,
        operator_skill="EXPERT", target_cycles=50, weather="SUNNY", rainfall_mm_h=0,
        visibility_m=5000, wind_kmh=5, ambient_temp_c=22, ground="DRY", hauler_queue_mean=1.5,
    )
    base.update(overrides)
    return base


def test_predict_task_returns_ordered_quantiles(registry):
    pred = eta.predict_task(_ctx(), registry)
    assert pred is not None
    assert pred.p10_min <= pred.p50_min <= pred.p90_min


def test_shap_additivity(registry):
    """base + sum(factor minutes) must equal p50 EXACTLY (to float precision) —
    the residual-absorption in predict_task() guarantees the displayed
    breakdown always adds up (ARCH 6.8), not just within a loose tolerance."""
    weathers = ["SUNNY", "RAIN", "WINDY"]
    skills = ["EXPERT", "INTERMEDIATE", "BEGINNER"]
    checked = 0
    for w in weathers:
        for s in skills:
            pred = eta.predict_task(_ctx(weather=w, operator_skill=s), registry)
            total = pred.base_value_min + sum(f.minutes for f in pred.factors)
            assert abs(total - pred.p50_min) < 0.01, f"{w}/{s}: base+factors={total} vs p50={pred.p50_min}"
            checked += 1
    assert checked >= 9


def test_factor_groups_match_constants(registry):
    from ml.constants import ETA_FACTOR_GROUPS
    pred = eta.predict_task(_ctx(), registry)
    groups = {f.group for f in pred.factors}
    assert groups == set(ETA_FACTOR_GROUPS.keys())


def test_rain_gives_positive_weather_factor_direction(registry):
    """Switching SUNNY -> RAIN (all else equal) should increase the total ETA."""
    sunny = eta.predict_task(_ctx(weather="SUNNY"), registry)
    rain = eta.predict_task(_ctx(weather="RAIN", rainfall_mm_h=25, visibility_m=150, ground="MUDDY"), registry)
    assert rain.p50_min > sunny.p50_min


def test_beginner_gives_higher_eta_than_expert(registry):
    expert = eta.predict_task(_ctx(operator_skill="EXPERT"), registry)
    beginner = eta.predict_task(_ctx(operator_skill="BEGINNER"), registry)
    assert beginner.p50_min > expert.p50_min


def test_predict_task_reports_defaults_used(registry):
    pred = eta.predict_task({"task_type": "GRADING", "machine_type": "LOADER"}, registry)
    assert "operator_skill" in pred.defaults_used
    assert "task_type" not in pred.defaults_used


# --- registry degradation --------------------------------------------------

def test_missing_artifacts_gives_unavailable(tmp_path):
    reg = ModelRegistry(artifacts_dir=str(tmp_path))  # empty dir
    reg.load()
    assert reg.eta_status == "MISSING"
    assert eta.predict_task(_ctx(), reg) is None


# --- blend -------------------------------------------------------------

def test_blend_pure_model_before_min_cycles():
    result = eta.blend(
        p50_total_min=40.0, p10_total_min=30.0, p90_total_min=55.0,
        elapsed_min=10.0, cycles_done=1, target_cycles=50,
        observed_cycle_s=50.0, eta_min_cycles_for_blend=3,
    )
    assert result.blend_weight_model == 1.0
    assert result.remaining_p50_min == pytest.approx(30.0)  # 40 - 10


def test_blend_weight_approaches_observed_near_completion():
    result = eta.blend(
        p50_total_min=40.0, p10_total_min=30.0, p90_total_min=55.0,
        elapsed_min=36.0, cycles_done=45, target_cycles=50,
        observed_cycle_s=48.0, eta_min_cycles_for_blend=3,
    )
    assert result.progress == pytest.approx(0.9)
    assert result.blend_weight_model == pytest.approx(0.1, abs=0.01)


def test_blend_zero_remaining_at_full_progress():
    result = eta.blend(
        p50_total_min=40.0, p10_total_min=30.0, p90_total_min=55.0,
        elapsed_min=42.0, cycles_done=50, target_cycles=50,
        observed_cycle_s=50.0, eta_min_cycles_for_blend=3,
    )
    assert result.progress == 1.0
    assert result.remaining_p50_min == pytest.approx(0.0, abs=0.01)


def test_blend_band_narrows_as_progress_increases():
    widths = []
    for cycles_done, elapsed in [(3, 5.0), (25, 20.0), (48, 38.0)]:
        r = eta.blend(
            p50_total_min=40.0, p10_total_min=30.0, p90_total_min=55.0,
            elapsed_min=elapsed, cycles_done=cycles_done, target_cycles=50,
            observed_cycle_s=48.0, eta_min_cycles_for_blend=3,
        )
        widths.append(r.remaining_p90_min - r.remaining_p10_min)
    assert widths[0] >= widths[1] >= widths[2]


# --- slip ----------------------------------------------------------------

def test_slip_below_threshold_no_event():
    slip_pct, band, severity = eta.slip(40.0, 43.6, last_emitted_band=0, slip_threshold=0.10, warning_threshold=0.25)
    assert slip_pct == pytest.approx(0.09, abs=0.001)
    assert band == 0
    assert severity is None


def test_slip_band1_info():
    slip_pct, band, severity = eta.slip(40.0, 44.8, last_emitted_band=0, slip_threshold=0.10, warning_threshold=0.25)
    assert slip_pct == pytest.approx(0.12, abs=0.001)
    assert band == 1
    assert severity == "INFO"


def test_slip_band2_warning():
    slip_pct, band, severity = eta.slip(40.0, 50.8, last_emitted_band=0, slip_threshold=0.10, warning_threshold=0.25)
    assert slip_pct == pytest.approx(0.27, abs=0.001)
    assert band == 2
    assert severity == "WARNING"


def test_slip_same_band_twice_fires_once():
    _, band1, sev1 = eta.slip(40.0, 44.8, last_emitted_band=0, slip_threshold=0.10, warning_threshold=0.25)
    assert sev1 == "INFO"
    _, band2, sev2 = eta.slip(40.0, 44.9, last_emitted_band=band1, slip_threshold=0.10, warning_threshold=0.25)
    assert sev2 is None  # same band as before -> no new event


# --- crossing guard --------------------------------------------------------

def test_crossing_guard_corrects_p10_above_p50(monkeypatch, registry):
    """Force the quantile model to report p10 > p50 and check predict_task corrects it."""
    class FakeQModel:
        def predict(self, X):
            return np.array([[999.0, 1.0]])  # p10=999 (nonsense), p90=1

    monkeypatch.setattr(registry, "eta_q_model", FakeQModel())
    pred = eta.predict_task(_ctx(), registry)
    assert pred.p10_min <= pred.p50_min <= pred.p90_min
