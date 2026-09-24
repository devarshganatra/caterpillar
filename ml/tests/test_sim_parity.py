"""
Train/serve parity guard (plan §Q8): ml.sim_physics is a separate
re-implementation of simulator/sim.py's physics for offline data generation.
If the two drift apart, the ETA/anomaly/attribution models trained on
ml.sim_physics output would be meaningless on live telemetry from sim.py.

This test runs the REAL simulator/sim.py physics for a WORKING machine and
checks that ml.features.compute_window_features on that real output falls
within the [P1, P99] range of ml.sim_physics-generated WORKING windows for
the same machine type. It is intentionally loose (a distributional check,
not an exact match) since the two use different RNG streams.
"""
import random

import numpy as np
import pytest

from contracts.machine_config import MACHINES
from ml.features import compute_window_features, WINDOW_FEATURES
from ml.sim_physics import simulate_window
from simulator.sim import create_initial_state, advance_state, SimMode

pytestmark = pytest.mark.unit


def _run_real_simulator(machine_id: str, n_frames: int, seed: int, warmup_frames: int = 300) -> dict:
    """
    Runs the actual simulator/sim.py physics loop (WORKING mode) and
    collects raw frame arrays, mirroring what the warm worker would see.

    `warmup_frames` are run and discarded first: the very first window after
    machine start ramps engine_temp_c up from an ambient 25C start, which
    ml.sim_physics.simulate_window does not model (it assumes a machine
    already mid-shift, start_temp_c=85 by default) — this only differs for
    that one-time startup window, not for the steady-running windows that
    make up the vast majority of both live telemetry and generated history,
    so the comparison here uses a warmed-up machine like the generator does.
    """
    cfg = MACHINES[machine_id]
    rng = random.Random(seed)
    state = create_initial_state(machine_id, rng)
    state.mode = SimMode.SIM_WORKING

    for _ in range(warmup_frames):
        advance_state(state, cfg, rng)

    rpm, hyd, fuel, temp = [], [], [], []
    cycle_completed = []
    for _ in range(n_frames):
        advance_state(state, cfg, rng)
        rpm.append(state.current_rpm)
        hyd.append(state.current_hyd_bar)
        fuel.append(state.current_fuel_lph)
        temp.append(state.current_temp_c)
        cycle_completed.append(state.cycle_completed)

    return {
        "engine_rpm": np.array(rpm),
        "hydraulic_pressure_bar": np.array(hyd),
        "fuel_rate_lph": np.array(fuel),
        "engine_temp_c": np.array(temp),
        "cycle_completed": np.array(cycle_completed, dtype=bool),
        "is_idle": np.zeros(n_frames, dtype=bool),
        "truck_present": np.ones(n_frames, dtype=bool),
        "hauler_queue_len": np.ones(n_frames),
    }


def _sim_physics_cfg(machine) -> dict:
    return {
        "rpm_idle_range": machine.rpm_idle_range,
        "rpm_working_range": machine.rpm_working_range,
        "hyd_working_range": machine.hyd_working_range,
        "speed_max_kmh": machine.speed_max_kmh,
        "fuel_rate_idle_lph": machine.fuel_rate_idle_lph,
        "fuel_rate_working_lph": machine.fuel_rate_working_lph,
    }


def test_fuel_multiplier_does_not_compound_across_frames():
    """
    Regression test: fuel_multiplier (used for the HIGH_FUEL_PER_CYCLE
    anomaly injection) must scale each frame's reported fuel_rate_lph by a
    constant factor, not compound onto the persistent EMA state every frame
    (which previously blew up to ~1e93 over a 300-frame window).
    """
    from contracts.machine_config import MACHINES
    machine = MACHINES["EXC001"]
    cfg = _sim_physics_cfg(machine)
    rng = np.random.default_rng(1)

    frames_normal, _ = simulate_window(rng, cfg, "working", 300)
    rng2 = np.random.default_rng(1)
    frames_anomalous, _ = simulate_window(rng2, cfg, "working", 300, fuel_multiplier=1.6)

    # Same RNG stream -> the anomalous run's fuel should be ~1.6x the normal
    # run's fuel at every frame (bounded ratio), never exponentially larger.
    ratio = frames_anomalous["fuel_rate_lph"] / np.maximum(frames_normal["fuel_rate_lph"], 1e-6)
    assert np.all(ratio < 3.0), f"fuel ratio blew up: max={ratio.max()}"
    assert np.mean(ratio) == pytest.approx(1.6, rel=0.2)


@pytest.mark.parametrize("machine_id", ["EXC001", "LDR001"])
def test_real_simulator_features_within_generator_distribution(machine_id):
    N_FRAMES = 300
    N_REFERENCE_WINDOWS = 40

    machine = MACHINES[machine_id]
    cfg = _sim_physics_cfg(machine)

    rng = np.random.default_rng(123)
    reference_rows = []
    phys_state = None
    for i in range(N_REFERENCE_WINDOWS):
        frames, phys_state = simulate_window(rng, cfg, "working", N_FRAMES, init_state=phys_state)
        reference_rows.append(compute_window_features(frames, sim_seconds_per_frame=1.0))
    # Drop the first reference window: it's the one-time cold-start ramp
    # (rpm/hyd/fuel spinning up from 0), same as the real sim's discarded
    # warmup — comparing steady-state windows on both sides.
    reference_rows = reference_rows[1:]

    real_frames = _run_real_simulator(machine_id, N_FRAMES, seed=7)
    real_feats = compute_window_features(real_frames, sim_seconds_per_frame=1.0)

    checked = 0
    for feat in WINDOW_FEATURES:
        ref_vals = [r[feat] for r in reference_rows if r[feat] is not None]
        if not ref_vals or real_feats[feat] is None:
            continue  # some features (fuel_per_cycle etc.) may be undefined in a low-cycle sample
        lo, hi = np.percentile(ref_vals, [1, 99])
        # Widen for small-sample percentile noise: this is a coarse
        # distributional sanity check (single real-sim sample vs 40
        # generator windows), not a tight statistical test.
        margin = 0.25 * max(abs(hi - lo), 1.0) + 0.03 * max(abs(hi), abs(lo), 1.0)
        assert (lo - margin) <= real_feats[feat] <= (hi + margin), (
            f"{feat}: real={real_feats[feat]} outside generator range "
            f"[{lo - margin}, {hi + margin}] (machine={machine_id})"
        )
        checked += 1

    assert checked >= 4, "expected at least a handful of comparable features"
