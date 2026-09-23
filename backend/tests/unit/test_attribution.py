"""Unit tests for backend.app.services.attribution: idle-cause priority rule + operator deviation."""
from datetime import datetime, timezone

import numpy as np
import pytest

from backend.app.services import attribution
from backend.app.services.ml_registry import ModelRegistry

CFG = {
    "weather_stop_rain_mm_h": 20.0, "weather_stop_visibility_m": 50.0,
    "weather_stop_wind_kmh": 60.0, "idle_dev_ratio": 2.0,
    "idle_dev_robust_z": 2.5, "idle_dev_persist_windows": 2, "idle_baseline_min_n": 20,
}


def _frame_args(n=10, temp=90.0, hyd=100.0, truck=True, queue=1, fault=False):
    return dict(
        n_frames=n, ts_start=datetime(2026, 1, 1, tzinfo=timezone.utc), sim_seconds_per_frame=1.0,
        engine_temp_c=np.full(n, temp), hydraulic_pressure_bar=np.full(n, hyd),
        truck_present=np.full(n, truck), hauler_queue_len=np.full(n, queue),
        context={}, planned_breaks_utc=[], fault_active=fault, cfg=CFG,
    )


class TestPriority:
    def test_planned_break_wins_over_everything(self):
        """A frame inside a planned break, with a fault AND heavy rain AND no truck, must still be PLANNED."""
        args = _frame_args(n=1, temp=110.0, fault=True, truck=False, queue=0)
        args["planned_breaks_utc"] = [("00:00", "00:05")]
        args["context"] = {"rainfall_mm_h": 30}
        causes = attribution.classify_idle_frames(**args)
        assert causes[0] == "PLANNED"

    def test_machine_wins_when_no_planned_break(self):
        args = _frame_args(n=1, temp=110.0, fault=True, truck=False, queue=0)
        args["context"] = {"rainfall_mm_h": 30}
        causes = attribution.classify_idle_frames(**args)
        assert causes[0] == "MACHINE"

    def test_weather_wins_when_no_machine_fault(self):
        args = _frame_args(n=1, temp=90.0, fault=False, truck=False, queue=0)
        args["context"] = {"rainfall_mm_h": 30}  # >= 20 threshold
        causes = attribution.classify_idle_frames(**args)
        assert causes[0] == "WEATHER"

    def test_site_when_only_no_truck(self):
        args = _frame_args(n=1, temp=90.0, fault=False, truck=False, queue=0)
        args["context"] = {"rainfall_mm_h": 0}
        causes = attribution.classify_idle_frames(**args)
        assert causes[0] == "SITE"

    def test_site_when_queue_zero_but_truck_present(self):
        args = _frame_args(n=1, temp=90.0, fault=False, truck=True, queue=0)
        causes = attribution.classify_idle_frames(**args)
        assert causes[0] == "SITE"

    def test_operator_when_truck_present_and_queue_nonzero(self):
        args = _frame_args(n=1, temp=90.0, fault=False, truck=True, queue=1)
        causes = attribution.classify_idle_frames(**args)
        assert causes[0] == "OPERATOR"

    def test_health_thresholds_trigger_machine_without_explicit_fault_flag(self):
        args = _frame_args(n=1, temp=110.0, fault=False, truck=True, queue=1)  # temp > 105
        causes = attribution.classify_idle_frames(**args)
        assert causes[0] == "MACHINE"


def test_attribute_window_breakdown_and_primary_cause():
    causes = np.array(["OPERATOR"] * 7 + ["SITE"] * 3)
    result = attribution.attribute_window(
        window_id="w1", machine_id="EXC001", operator_id="op1",
        window_start=datetime(2026, 1, 1), window_end=datetime(2026, 1, 1),
        causes=causes, sim_seconds_per_frame=1.0,
        truck_present_ratio=0.7, hauler_queue_mean=1.0,
        context={"weather": "SUNNY"}, fault_event_ids=[], planned_break=False,
    )
    assert result.breakdown_s["OPERATOR"] == 7.0
    assert result.breakdown_s["SITE"] == 3.0
    assert result.primary_cause == "OPERATOR"
    assert result.operator_idle_ratio == 0.7


class TestDeviation:
    def _registry(self, entries):
        reg = ModelRegistry(artifacts_dir="unused")
        reg.baselines_status = "READY"
        reg.idle_baselines = {"levels": entries, "key_separator": "||"}
        reg._loaded = True
        return reg

    def test_backoff_to_skill_level_when_operator_n_too_small(self):
        entries = {
            "operator_context": {"OP1||TRUCK_LOADING||SUNNY||EXPERT": {"n": 5, "ewma": 0.1, "median": 0.1, "mad": 0.02}},
            "skill_context": {"EXPERT||TRUCK_LOADING||SUNNY": {"n": 50, "ewma": 0.15, "median": 0.15, "mad": 0.03}},
            "task_type": {}, "global": {},
        }
        reg = self._registry(entries)
        result = attribution.operator_deviation(
            operator_idle_ratio=0.5, operator_id="OP1", task_type="TRUCK_LOADING",
            weather="SUNNY", operator_skill="EXPERT", prev_consecutive_raw_flags=0, cfg=CFG, registry=reg,
        )
        assert result.baseline_level == "skill_context"

    def test_single_raw_flag_does_not_set_flag(self):
        entries = {
            "operator_context": {"OP1||TRUCK_LOADING||SUNNY||EXPERT": {"n": 30, "ewma": 0.1, "median": 0.1, "mad": 0.02}},
            "skill_context": {}, "task_type": {}, "global": {},
        }
        reg = self._registry(entries)
        # ratio = 0.5/0.1 = 5 >= 2.0, z = (0.5-0.1)/(1.4826*0.02) huge -> raw_flag True, but only 1st window
        result = attribution.operator_deviation(
            operator_idle_ratio=0.5, operator_id="OP1", task_type="TRUCK_LOADING",
            weather="SUNNY", operator_skill="EXPERT", prev_consecutive_raw_flags=0, cfg=CFG, registry=reg,
        )
        assert result.raw_flag is True
        assert result.operator_deviation_flag is False  # persist_windows=2, this is only the 1st

    def test_two_consecutive_raw_flags_sets_flag(self):
        entries = {
            "operator_context": {"OP1||TRUCK_LOADING||SUNNY||EXPERT": {"n": 30, "ewma": 0.1, "median": 0.1, "mad": 0.02}},
            "skill_context": {}, "task_type": {}, "global": {},
        }
        reg = self._registry(entries)
        result = attribution.operator_deviation(
            operator_idle_ratio=0.5, operator_id="OP1", task_type="TRUCK_LOADING",
            weather="SUNNY", operator_skill="EXPERT", prev_consecutive_raw_flags=1, cfg=CFG, registry=reg,
        )
        assert result.raw_flag is True
        assert result.operator_deviation_flag is True

    def test_missing_baselines_gives_unavailable(self):
        reg = ModelRegistry(artifacts_dir="unused")
        reg.baselines_status = "MISSING"
        reg._loaded = True
        result = attribution.operator_deviation(
            operator_idle_ratio=0.5, operator_id="OP1", task_type="TRUCK_LOADING",
            weather="SUNNY", operator_skill="EXPERT", prev_consecutive_raw_flags=0, cfg=CFG, registry=reg,
        )
        assert result.status == "UNAVAILABLE"
        assert result.operator_deviation_flag is False


# --- integration-flavored check against the real generated ground truth ---

def test_not_everything_is_operator_attributed_on_real_dataset():
    import pandas as pd
    import os
    if not os.path.exists("ml/data/windows.csv.gz"):
        pytest.skip("ml/data/windows.csv.gz not present — run `make gen-data` first")
    df = pd.read_csv("ml/data/windows.csv.gz")
    df = df[df["split"] == "test"]
    idle_cols = [f"idle_s_{c}" for c in ["PLANNED", "MACHINE", "WEATHER", "SITE", "OPERATOR"]]
    idle_df = df[df[idle_cols].sum(axis=1) > 0]
    totals = idle_df[idle_cols].sum()
    total = totals.sum()
    assert totals["idle_s_OPERATOR"] / total < 1.0
    assert totals["idle_s_SITE"] > 0
