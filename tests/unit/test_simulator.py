"""Unit tests for simulator/sim.py fixes (G9): deterministic assignment + hauler queue model."""
import random

from simulator.sim import (
    create_initial_state, advance_state, apply_scenario_overrides,
    build_telemetry_frame, SimMode, MachineRunState,
)
from contracts.machine_config import MACHINES
from contracts.demo_assignments import DEMO_ASSIGNMENTS


def test_default_assignment_matches_seeded_tasks():
    state = create_initial_state("EXC001", random.Random(1))
    assert state.operator_id == DEMO_ASSIGNMENTS["EXC001"].operator_id
    assert state.task_id == "TASK-001"


def test_unassigned_machine_gets_fallback_task_id():
    state = create_initial_state("EXC003", random.Random(1))
    assert state.operator_id == "UNASSIGNED"
    assert state.task_id.startswith("TSK_")


def test_random_ids_flag_restores_old_behaviour():
    state = create_initial_state("EXC001", random.Random(1), random_ids=True)
    assert state.operator_id.startswith("OP_")
    assert state.task_id.startswith("TSK_")


def test_initial_state_has_a_truck_present():
    # Regression guard for G9: previously truck_present was hardcoded False
    # in build_telemetry_frame regardless of simulator state.
    state = create_initial_state("EXC001", random.Random(1))
    frame = build_telemetry_frame(state)
    assert frame.truck_present is True
    assert frame.hauler_queue_len >= 1


def test_hauler_queue_len_zero_when_truck_absent():
    state = create_initial_state("EXC001", random.Random(1))
    state.truck_present = False
    state.hauler_queue_len = 2
    rng = random.Random(1)
    config = MACHINES["EXC001"]
    # Advance many ticks; whenever truck_present ends up False, queue must be 0
    for _ in range(200):
        advance_state(state, config, rng)
        if not state.truck_present:
            assert state.hauler_queue_len == 0


def test_hauler_queue_len_bounded_when_truck_present():
    state = create_initial_state("EXC001", random.Random(2))
    rng = random.Random(2)
    config = MACHINES["EXC001"]
    for _ in range(200):
        advance_state(state, config, rng)
        assert 0 <= state.hauler_queue_len <= 3
        if state.truck_present:
            assert state.hauler_queue_len >= 1


def test_scenario_override_truck_absent():
    state = create_initial_state("EXC001", random.Random(1))
    events = [{"at": 5, "machine_id": "EXC001", "set": {"truck_present": False, "hauler_queue_len": 0}}]
    apply_scenario_overrides(state, events, elapsed=5)
    assert state.truck_present is False
    assert state.hauler_queue_len == 0


def test_scenario_override_mode_converts_to_enum():
    state = create_initial_state("EXC001", random.Random(1))
    events = [{"at": 3, "machine_id": "EXC001", "set": {"mode": "idle"}}]
    apply_scenario_overrides(state, events, elapsed=3)
    assert state.mode == SimMode.SIM_IDLE


def test_reproducible_hauler_sequence_for_same_seed():
    s1 = create_initial_state("EXC001", random.Random(7))
    s2 = create_initial_state("EXC001", random.Random(7))
    rng1, rng2 = random.Random(7), random.Random(7)
    config = MACHINES["EXC001"]
    seq1, seq2 = [], []
    for _ in range(50):
        advance_state(s1, config, rng1)
        advance_state(s2, config, rng2)
        seq1.append((s1.truck_present, s1.hauler_queue_len))
        seq2.append((s2.truck_present, s2.hauler_queue_len))
    assert seq1 == seq2
