import json

import numpy as np
import pytest

from ml.generate_history import generate
from ml.train_eta import train

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    out_dir = tmp_path_factory.mktemp("artifacts")
    generate("tiny", seed=42, out_dir=str(data_dir))
    meta = train(str(data_dir), str(out_dir), seed=42)
    return data_dir, out_dir, meta


def test_training_writes_all_artifacts(trained):
    _, out_dir, meta = trained
    assert (out_dir / "eta_p50.json").exists()
    assert (out_dir / "eta_q.json").exists()
    assert (out_dir / "eta_background.npz").exists()
    assert (out_dir / "eta_meta.json").exists()
    assert meta["n_train"] > 0


def test_feature_columns_stable_across_runs(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data2")
    out1 = tmp_path_factory.mktemp("out1")
    out2 = tmp_path_factory.mktemp("out2")
    generate("tiny", seed=42, out_dir=str(data_dir))
    m1 = train(str(data_dir), str(out1), seed=42)
    m2 = train(str(data_dir), str(out2), seed=42)
    assert m1["feature_columns"] == m2["feature_columns"]


def test_val_mae_is_finite_and_positive(trained):
    _, _, meta = trained
    assert meta["val_mae_p50"] >= 0
    assert np.isfinite(meta["val_mae_p50"])
