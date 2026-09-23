import numpy as np
import pytest

from ml.generate_history import generate
from ml.train_anomaly import train


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    out_dir = tmp_path_factory.mktemp("artifacts")
    generate("dev", seed=42, out_dir=str(data_dir))
    meta = train(str(data_dir), str(out_dir), seed=42)
    return data_dir, out_dir, meta


def test_writes_all_artifacts(trained):
    _, out_dir, meta = trained
    assert (out_dir / "iforest.joblib").exists()
    assert (out_dir / "iforest_meta.json").exists()
    assert (out_dir / "window_stats.json").exists()
    assert meta["n_train"] > 0


def test_score_quantiles_monotonic(trained):
    _, _, meta = trained
    q = np.array(meta["score_quantiles"])
    assert len(q) == 1001
    assert np.all(np.diff(q) >= 0)


def test_window_stats_has_all_features(trained):
    import json
    _, out_dir, _ = trained
    stats = json.load(open(out_dir / "window_stats.json"))
    from ml.constants import WINDOW_FEATURES
    for f in WINDOW_FEATURES:
        assert f in stats
        assert "median" in stats[f] and "mad" in stats[f]


def test_calibrated_score_in_unit_range(trained):
    import joblib
    data_dir, out_dir, meta = trained
    import pandas as pd
    from ml.constants import WINDOW_FEATURES

    model = joblib.load(out_dir / "iforest.joblib")
    df = pd.read_csv(data_dir / "windows.csv.gz")
    df = df[(df["working_ratio"] >= 0.5) & (df["cycle_count"] >= 2)]
    for f in WINDOW_FEATURES:
        df = df[df[f].notna()]
    X = df[WINDOW_FEATURES].values.astype(float)

    raw = -model.score_samples(X)
    q = np.array(meta["score_quantiles"])
    calibrated = np.clip(np.searchsorted(q, raw) / 1000.0, 0, 1)
    assert calibrated.min() >= 0.0
    assert calibrated.max() <= 1.0

    # monotonic in raw score: higher raw outlier score -> calibrated percentile never decreases
    order = np.argsort(raw)
    assert np.all(np.diff(calibrated[order]) >= -1e-9)
