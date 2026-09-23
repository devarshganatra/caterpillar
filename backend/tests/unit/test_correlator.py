"""Unit tests for backend.app.services.correlator's pure decision logic."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.correlator import (
    decide, IncidentView, EventView, is_within_correlation_window,
    timeline_entry_key, merge_into_entry, new_entry_for_event, summarize_event,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def ev(event_id="e1", type="SEATBELT_VIOLATION", severity="WARNING", ts=T0, evidence=None) -> EventView:
    return EventView(event_id=event_id, type=type, severity=severity, ts=ts, machine_id="EXC001", evidence=evidence)


def inc(severity="WARNING", status="OPEN", opened_at=T0, last_event_at=T0, escalated=False) -> IncidentView:
    return IncidentView(id="i1", severity=severity, status=status, opened_at=opened_at,
                         last_event_at=last_event_at, escalated=escalated)


class TestDecide:
    def test_warning_with_no_active_opens(self):
        d = decide(ev(severity="WARNING"), None, "NORMAL", 300)
        assert d.action == "OPEN"

    def test_critical_with_no_active_opens(self):
        d = decide(ev(severity="CRITICAL"), None, "NORMAL", 300)
        assert d.action == "OPEN"

    def test_info_with_no_active_ignored(self):
        d = decide(ev(severity="INFO"), None, "NORMAL", 300)
        assert d.action == "IGNORE_INFO"

    def test_active_incident_extends(self):
        d = decide(ev(severity="WARNING"), inc(severity="WARNING"), "NORMAL", 300)
        assert d.action == "EXTEND"
        assert d.new_severity == "WARNING"

    def test_escalation_on_higher_severity(self):
        d = decide(ev(severity="CRITICAL"), inc(severity="WARNING"), "NORMAL", 300)
        assert d.action == "EXTEND"
        assert d.escalate is True
        assert d.new_severity == "CRITICAL"

    def test_no_escalation_on_equal_or_lower_severity(self):
        d = decide(ev(severity="WARNING"), inc(severity="CRITICAL"), "NORMAL", 300)
        assert d.escalate is False
        assert d.new_severity == "CRITICAL"  # stays at the max

    def test_escalation_when_risk_high_even_without_severity_increase(self):
        d = decide(ev(severity="WARNING"), inc(severity="WARNING", escalated=False), "HIGH", 300)
        assert d.escalate is True

    def test_no_double_escalation_when_already_escalated(self):
        d = decide(ev(severity="WARNING"), inc(severity="WARNING", escalated=True), "HIGH", 300)
        assert d.escalate is False


class TestCorrelationWindow:
    def test_within_window_true(self):
        assert is_within_correlation_window(T0 + timedelta(minutes=4), T0, T0, 300)

    def test_outside_window_false(self):
        assert not is_within_correlation_window(T0 + timedelta(minutes=6), T0, T0, 300)

    def test_boundary_exact(self):
        assert is_within_correlation_window(T0 + timedelta(seconds=300), T0, T0, 300)


class TestTimeline:
    def test_entry_key_includes_zone(self):
        assert timeline_entry_key(ev(type="PROXIMITY_BREACH", evidence={"zone": "RED"})) == "PROXIMITY_BREACH:RED"

    def test_entry_key_no_evidence(self):
        assert timeline_entry_key(ev(type="SEATBELT_VIOLATION")) == "SEATBELT_VIOLATION:"

    def test_merge_within_window(self):
        entry = new_entry_for_event(ev(event_id="e1", ts=T0))
        merged = merge_into_entry(entry, ev(event_id="e2", ts=T0 + timedelta(seconds=10)), merge_window_s=30)
        assert merged is not None
        assert merged.count == 2
        assert merged.last_ts == T0 + timedelta(seconds=10)

    def test_no_merge_outside_window(self):
        entry = new_entry_for_event(ev(event_id="e1", ts=T0))
        merged = merge_into_entry(entry, ev(event_id="e2", ts=T0 + timedelta(seconds=60)), merge_window_s=30)
        assert merged is None

    def test_no_merge_different_key(self):
        entry = new_entry_for_event(ev(event_id="e1", type="SEATBELT_VIOLATION", ts=T0))
        merged = merge_into_entry(entry, ev(event_id="e2", type="PROXIMITY_BREACH", ts=T0 + timedelta(seconds=1)))
        assert merged is None

    def test_100_events_merge_to_one_entry_with_count_100(self):
        entry = new_entry_for_event(ev(event_id="e0", ts=T0))
        for i in range(1, 100):
            merged = merge_into_entry(entry, ev(event_id=f"e{i}", ts=T0 + timedelta(seconds=i * 0.1)), merge_window_s=30)
            assert merged is not None
            entry = merged
        assert entry.count == 100

    def test_summarize_known_type(self):
        s = summarize_event(ev(type="SEATBELT_VIOLATION", evidence={"state": "WORKING"}))
        assert "Seatbelt" in s and "WORKING" in s

    def test_summarize_unknown_type_falls_back(self):
        s = summarize_event(ev(type="SOME_NEW_TYPE", severity="INFO"))
        assert "SOME_NEW_TYPE" in s
