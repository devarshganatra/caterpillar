import pytest

from ml.generate_history import generate
from ml.train_eta import train as train_eta
from ml.train_anomaly import train as train_anomaly
from ml.baselines import build_baselines
from ml.evaluate import evaluate_eta, evaluate_anomaly, evaluate_attribution

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    out_dir = tmp_path_factory.mktemp("artifacts")
    generate("dev", seed=42, out_dir=str(data_dir))
    train_eta(str(data_dir), str(out_dir), seed=42)
    train_anomaly(str(data_dir), str(out_dir), seed=42)
    import json
    baselines = build_baselines(str(data_dir))
    with open(out_dir / "idle_baselines.json", "w") as f:
        json.dump(baselines, f)
    return data_dir, out_dir


def test_eta_metrics_are_test_split(pipeline):
    data_dir, out_dir = pipeline
    m = evaluate_eta(str(data_dir), str(out_dir))
    assert m["split"] == "test"
    assert m["n"] > 0
    assert m["data"] == "synthetic"
    assert m["mae_model_p50_min"] >= 0


def test_anomaly_metrics_are_test_split(pipeline):
    data_dir, out_dir = pipeline
    m = evaluate_anomaly(str(data_dir), str(out_dir))
    assert m["split"] == "test"
    assert m["n"] > 0
    assert "isolation_forest" in m
    assert "robust_z_fallback" in m
    assert 0.0 <= m["isolation_forest"]["precision"] <= 1.0
    assert 0.0 <= m["isolation_forest"]["recall"] <= 1.0


def test_attribution_metrics_are_test_split(pipeline):
    data_dir, _ = pipeline
    m = evaluate_attribution(str(data_dir))
    assert m["split"] == "test"
    assert m["n"] > 0
    assert 0.0 <= m["primary_cause_agreement"] <= 1.0


def test_train_test_indices_disjoint(pipeline):
    data_dir, _ = pipeline
    import pandas as pd
    windows = pd.read_csv(data_dir / "windows.csv.gz")
    train_days = set(windows[windows["split"] == "train"]["day"])
    test_days = set(windows[windows["split"] == "test"]["day"])
    assert train_days.isdisjoint(test_days)
