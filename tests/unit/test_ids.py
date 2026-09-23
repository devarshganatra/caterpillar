"""Unit tests for contracts/ids.py — determinism is the whole point."""
from contracts.ids import (
    hot_event_id, warm_event_id, eta_slip_event_id, incident_id, window_id,
)


def test_hot_event_id_deterministic():
    a = hot_event_id("EXC001", 42, "SEATBELT_VIOLATION", {"state": "WORKING"})
    b = hot_event_id("EXC001", 42, "SEATBELT_VIOLATION", {"state": "WORKING"})
    assert a == b


def test_hot_event_id_distinguishes_frame_seq():
    a = hot_event_id("EXC001", 42, "SEATBELT_VIOLATION", {})
    b = hot_event_id("EXC001", 43, "SEATBELT_VIOLATION", {})
    assert a != b


def test_hot_event_id_distinguishes_evidence_zone():
    a = hot_event_id("EXC001", 42, "PROXIMITY_BREACH", {"zone": "RED"})
    b = hot_event_id("EXC001", 42, "PROXIMITY_BREACH", {"zone": "ORANGE"})
    assert a != b


def test_hot_event_id_distinguishes_machine():
    a = hot_event_id("EXC001", 42, "SEATBELT_VIOLATION", {})
    b = hot_event_id("EXC002", 42, "SEATBELT_VIOLATION", {})
    assert a != b


def test_hot_event_id_is_valid_uuid_string():
    import uuid
    val = hot_event_id("EXC001", 1, "X", {})
    # Should not raise
    uuid.UUID(val)


def test_warm_event_id_deterministic_and_distinct():
    a = warm_event_id("EXC001:1000", "OPERATIONAL_ANOMALY")
    b = warm_event_id("EXC001:1000", "OPERATIONAL_ANOMALY")
    c = warm_event_id("EXC001:1030", "OPERATIONAL_ANOMALY")
    assert a == b
    assert a != c


def test_eta_slip_event_id_per_band():
    a = eta_slip_event_id("TASK-001", 1)
    b = eta_slip_event_id("TASK-001", 1)
    c = eta_slip_event_id("TASK-001", 2)
    assert a == b
    assert a != c


def test_incident_id_deterministic():
    a = incident_id("EXC001", "some-event-id")
    b = incident_id("EXC001", "some-event-id")
    assert a == b


def test_window_id_buckets_correctly():
    # window_s = 30: window [990, 1020) covers ts 990.0 .. 1019.9; 1020.0 starts the next window
    w1 = window_id("EXC001", 990.0, 30)
    w2 = window_id("EXC001", 1019.9, 30)
    w3 = window_id("EXC001", 1020.0, 30)
    assert w1 == w2
    assert w1 != w3
    assert w1 == "EXC001:990"
    assert w3 == "EXC001:1020"
