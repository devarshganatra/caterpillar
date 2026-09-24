import numpy as np
import pytest

from ml.features import compute_window_features, robust_z, eta_feature_row, eta_feature_columns, encode_eta_row

pytestmark = pytest.mark.unit


def _frames(n, cycle_indices=(), rpm=1900.0, temp_start=85.0, temp_end=85.0, hyd=250.0, fuel=15.0, idle=False):
    cycle_completed = np.zeros(n, dtype=bool)
    for i in cycle_indices:
        cycle_completed[i] = True
    return {
        "engine_rpm": np.full(n, rpm),
        "engine_temp_c": np.linspace(temp_start, temp_end, n),
        "hydraulic_pressure_bar": np.full(n, hyd),
        "fuel_rate_lph": np.full(n, fuel),
        "cycle_completed": cycle_completed,
        "is_idle": np.full(n, idle, dtype=bool),
        "truck_present": np.ones(n, dtype=bool),
        "hauler_queue_len": np.ones(n),
    }


def test_known_cycle_count_and_mean():
    # 10 frames, cycles complete at frame index 4 and 9 -> one inter-cycle gap of 5 frames = 5s
    frames = _frames(10, cycle_indices=(4, 9))
    feats = compute_window_features(frames, sim_seconds_per_frame=1.0)
    assert feats["cycle_count"] == 2
    assert feats["cycle_time_mean"] == pytest.approx(5.0)


def test_fewer_than_two_cycles_gives_none():
    frames = _frames(10, cycle_indices=(5,))
    feats = compute_window_features(frames)
    assert feats["cycle_count"] == 1
    assert feats["cycle_time_mean"] is None
    assert feats["cycle_time_cv"] is None
    assert feats["fuel_per_cycle"] is None


def test_zero_cycles():
    frames = _frames(10, cycle_indices=())
    feats = compute_window_features(frames)
    assert feats["cycle_count"] == 0
    assert feats["fuel_per_cycle"] is None


def test_empty_window():
    feats = compute_window_features({"engine_rpm": np.array([])})
    assert feats["frame_count"] == 0
    assert feats["cycle_count"] == 0


def test_idle_ratio():
    frames = _frames(10, idle=True, hyd=30.0)
    feats = compute_window_features(frames)
    assert feats["idle_ratio"] == 1.0
    assert feats["working_ratio"] == 0.0  # working_ratio is derived independently from hydraulics, not the is_idle flag


def test_working_ratio_from_hydraulics():
    frames = _frames(10, hyd=250.0)  # >= 120 -> counted as working
    feats = compute_window_features(frames)
    assert feats["working_ratio"] == 1.0
    frames_low = _frames(10, hyd=30.0)
    feats_low = compute_window_features(frames_low)
    assert feats_low["working_ratio"] == 0.0


def test_temp_slope_positive_for_rising_temp():
    frames = _frames(60, temp_start=60.0, temp_end=90.0)  # +30C over 60s = 1 min -> slope 30 degC/min
    feats = compute_window_features(frames, sim_seconds_per_frame=1.0)
    assert feats["temp_slope"] == pytest.approx(30.0, rel=0.05)


def test_robust_z_zero_mad():
    assert robust_z(10.0, median=5.0, mad=0.0) == 0.0


def test_robust_z_nonzero():
    z = robust_z(10.0, median=5.0, mad=1.0)
    assert z == pytest.approx((10.0 - 5.0) / (1.4826 * 1.0))


def test_eta_feature_row_fills_defaults():
    row = eta_feature_row({"task_type": "GRADING"})
    assert row["task_type"] == "GRADING"
    assert "task_type" not in row["_defaults_used"]
    assert "operator_skill" in row["_defaults_used"]
    assert row["operator_skill"] == "INTERMEDIATE"


def test_encode_eta_row_matches_columns():
    cols = eta_feature_columns()
    row = eta_feature_row({"task_type": "GRADING", "machine_type": "LOADER"})
    vec = encode_eta_row(row, cols)
    assert len(vec) == len(cols)
    # exactly one one-hot column per categorical field should be 1
    task_type_cols = [c for c in cols if c.startswith("task_type__")]
    idx = [cols.index(c) for c in task_type_cols]
    assert sum(vec[i] for i in idx) == 1.0
