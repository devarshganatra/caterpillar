import json
import pandas as pd
import pytest

from ml.generate_history import generate
from ml.constants import IDLE_CAUSES

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def tiny_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("ml_gen_tiny")
    manifest = generate("tiny", seed=42, out_dir=str(out))
    return out, manifest


@pytest.fixture(scope="module")
def dev_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("ml_gen_dev")
    manifest = generate("dev", seed=42, out_dir=str(out))
    return out, manifest


def test_determinism_same_seed_same_hash(tmp_path_factory):
    out1 = tmp_path_factory.mktemp("run1")
    out2 = tmp_path_factory.mktemp("run2")
    m1 = generate("tiny", seed=42, out_dir=str(out1))
    m2 = generate("tiny", seed=42, out_dir=str(out2))
    assert m1["content_hash"] == m2["content_hash"]


def test_different_seed_different_hash(tmp_path_factory):
    out1 = tmp_path_factory.mktemp("run_a")
    out2 = tmp_path_factory.mktemp("run_b")
    m1 = generate("tiny", seed=42, out_dir=str(out1))
    m2 = generate("tiny", seed=43, out_dir=str(out2))
    assert m1["content_hash"] != m2["content_hash"]


def test_manifest_and_files_written(tiny_run):
    out, manifest = tiny_run
    assert (out / "tasks.csv.gz").exists()
    assert (out / "windows.csv.gz").exists()
    assert (out / "weather.csv.gz").exists()
    assert (out / "entities.json").exists()
    assert (out / "manifest.json").exists()
    assert manifest["rows"]["tasks"] > 0
    assert manifest["rows"]["windows"] > 0


def test_windows_have_expected_columns(tiny_run):
    out, _ = tiny_run
    df = pd.read_csv(out / "windows.csv.gz")
    for col in ["machine_id", "site_id", "operator_id", "split", "window_s", "is_anomaly", "anomaly_type"]:
        assert col in df.columns
    for cause in IDLE_CAUSES:
        assert f"idle_s_{cause}" in df.columns


def test_no_day_overlap_between_splits(tiny_run):
    out, _ = tiny_run
    df = pd.read_csv(out / "tasks.csv.gz")
    train_days = set(df[df["split"] == "train"]["day"])
    test_days = set(df[df["split"] == "test"]["day"])
    assert train_days.isdisjoint(test_days)
    if train_days and test_days:
        assert max(train_days) < min(test_days), "test days must be strictly later than train days"


def test_anomaly_rate_in_reasonable_range(dev_run):
    # tiny profile is small/noisy; use dev for a tighter check
    out, _ = dev_run
    df = pd.read_csv(out / "windows.csv.gz")
    rate = df["is_anomaly"].mean()
    assert 0.01 <= rate <= 0.05, f"anomaly rate {rate} outside expected [1%, 5%] band"


def test_not_all_idle_is_operator_attributed(tiny_run):
    """Sanity check on the generator's own idle-cause distribution: it must
    not label everything OPERATOR (that would defeat the point of attribution)."""
    out, _ = tiny_run
    df = pd.read_csv(out / "windows.csv.gz")
    idle_df = df[df[[f"idle_s_{c}" for c in IDLE_CAUSES]].sum(axis=1) > 0]
    assert len(idle_df) > 0
    totals = {c: idle_df[f"idle_s_{c}"].sum() for c in IDLE_CAUSES}
    total_idle = sum(totals.values())
    assert totals["OPERATOR"] / total_idle < 0.95
    assert totals["SITE"] > 0


def test_beginner_slower_than_expert_on_average(dev_run):
    out, _ = dev_run
    df = pd.read_csv(out / "tasks.csv.gz")
    ratio = df["actual_min"] / df["planner_estimate_min"].replace(0, pd.NA)
    df = df.assign(ratio=ratio)
    beginner_mean = df[df["operator_skill"] == "BEGINNER"]["ratio"].mean()
    expert_mean = df[df["operator_skill"] == "EXPERT"]["ratio"].mean()
    assert beginner_mean > expert_mean


def test_rain_tasks_longer_than_sunny_on_average(dev_run):
    out, _ = dev_run
    df = pd.read_csv(out / "tasks.csv.gz")
    rain_ratio = (df[df["weather"] == "RAIN"]["actual_min"] / df[df["weather"] == "RAIN"]["planner_estimate_min"]).mean()
    sunny_ratio = (df[df["weather"] == "SUNNY"]["actual_min"] / df[df["weather"] == "SUNNY"]["planner_estimate_min"]).mean()
    assert rain_ratio > sunny_ratio
