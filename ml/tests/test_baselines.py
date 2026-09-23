import pytest

from ml.generate_history import generate
from ml.baselines import build_baselines


@pytest.fixture(scope="module")
def baselines(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    generate("dev", seed=42, out_dir=str(data_dir))
    return build_baselines(str(data_dir))


def test_all_levels_populated(baselines):
    """Regression guard for the task_type NaN/groupby-drop bug: every level
    must have at least one entry, not silently empty."""
    for level_name, entries in baselines["levels"].items():
        assert len(entries) > 0, f"level {level_name} is empty"


def test_global_level_has_single_entry(baselines):
    assert list(baselines["levels"]["global"].keys()) == ["global"]


def test_stats_have_required_fields(baselines):
    for level in baselines["levels"].values():
        for key, stats in level.items():
            assert set(stats.keys()) == {"n", "ewma", "median", "mad"}
            assert stats["n"] > 0
            assert 0.0 <= stats["median"] <= 1.0
            assert 0.0 <= stats["ewma"] <= 1.0


def test_operator_context_keys_use_separator(baselines):
    from ml.baselines import KEY_SEP
    keys = list(baselines["levels"]["operator_context"].keys())
    assert all(KEY_SEP in k for k in keys)
