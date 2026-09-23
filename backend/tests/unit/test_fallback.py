"""Unit tests for backend.app.genai.fallback."""
import pytest

from backend.app.genai.fallback import build_fallback
from backend.app.genai.validator import validate_grounding

EVENT_ID_1 = "11111111-1111-1111-1111-111111111111"
EVENT_ID_2 = "22222222-2222-2222-2222-222222222222"
TRIGGER_ID = EVENT_ID_1


def make_packet(**overrides) -> dict:
    packet = {
        "incident": {
            "id": "inc-1", "machine_id": "EXC001", "site_id": "SITE-A", "severity": "CRITICAL",
            "escalated": True, "status": "OPEN", "opened_at": "2026-01-01T00:00:00Z",
            "last_event_at": "2026-01-01T00:05:00Z", "category": "SEATBELT_VIOLATION",
            "trigger_event_id": TRIGGER_ID,
        },
        "timeline": [
            {"entry_key": "SEATBELT_VIOLATION:", "kind": "EVENT", "event_type": "SEATBELT_VIOLATION",
             "severity": "CRITICAL", "first_ts": "2026-01-01T00:00:00Z", "last_ts": "2026-01-01T00:01:00Z",
             "count": 10, "summary": "Seatbelt unfastened while WORKING", "representative_event_id": EVENT_ID_1},
            {"entry_key": "PROXIMITY_BREACH:ORANGE", "kind": "EVENT", "event_type": "PROXIMITY_BREACH",
             "severity": "WARNING", "first_ts": "2026-01-01T00:02:00Z", "last_ts": "2026-01-01T00:03:00Z",
             "count": 5, "summary": "Proximity breach in ORANGE zone", "representative_event_id": EVENT_ID_2},
        ],
        "events": [
            {"event_id": EVENT_ID_1, "type": "SEATBELT_VIOLATION", "severity": "CRITICAL",
             "ts": "2026-01-01T00:00:00Z", "source_engine": "safety@1.0", "evidence": {"state": "WORKING"}},
            {"event_id": EVENT_ID_2, "type": "PROXIMITY_BREACH", "severity": "WARNING",
             "ts": "2026-01-01T00:02:00Z", "source_engine": "safety@1.0", "evidence": {"zone": "ORANGE"}},
        ],
        "knowledge": [
            {"chunk_id": "seatbelt-safety#overview", "title": "Seatbelt Safety", "heading": "Overview",
             "text": "Seatbelts are the single most effective protection. More detail here.",
             "tags": ["SEATBELT_VIOLATION"]},
            {"chunk_id": "proximity-awareness#overview", "title": "Proximity Awareness", "heading": "Overview",
             "text": "Struck-by incidents are a leading cause of injury. More detail here.",
             "tags": ["PROXIMITY_BREACH"]},
        ],
        "allowed_event_ids": [EVENT_ID_1, EVENT_ID_2],
        "allowed_chunk_ids": ["seatbelt-safety#overview", "proximity-awareness#overview"],
    }
    packet.update(overrides)
    return packet


def test_fallback_passes_grounding_validation():
    packet = make_packet()
    output = build_fallback(packet, "TIMEOUT")
    violations = validate_grounding(output, packet)
    assert violations == []


def test_fallback_is_deterministic():
    packet = make_packet()
    a = build_fallback(packet, "TIMEOUT")
    b = build_fallback(packet, "TIMEOUT")
    assert a.model_dump() == b.model_dump()


def test_fallback_confidence_is_low():
    output = build_fallback(make_packet(), "NO_API_KEY")
    assert output.confidence == "LOW"


def test_fallback_summary_mentions_reason():
    output = build_fallback(make_packet(), "GROUNDING_INVALID")
    assert "GROUNDING_INVALID" in output.summary


def test_fallback_cause_uses_evidence_template():
    output = build_fallback(make_packet(), "TIMEOUT")
    seatbelt_causes = [c for c in output.probable_causes if "WORKING" in c.cause]
    assert seatbelt_causes, f"expected a seatbelt cause mentioning WORKING state, got {output.probable_causes}"


def test_fallback_evidence_refs_are_real_event_ids():
    packet = make_packet()
    output = build_fallback(packet, "TIMEOUT")
    allowed = set(packet["allowed_event_ids"])
    for cause in output.probable_causes:
        for ref in cause.evidence_refs:
            assert ref in allowed


def test_fallback_knowledge_refs_are_real_chunk_ids():
    packet = make_packet()
    output = build_fallback(packet, "TIMEOUT")
    allowed = set(packet["allowed_chunk_ids"])
    assert set(output.lesson.knowledge_refs) <= allowed
    for action in output.recommended_actions:
        assert set(action.knowledge_refs) <= allowed


def test_fallback_with_unknown_event_type_uses_default_text():
    packet = make_packet()
    packet["timeline"][0]["event_type"] = "SOME_NEW_TYPE"
    packet["timeline"][0]["representative_event_id"] = EVENT_ID_1
    output = build_fallback(packet, "TIMEOUT")
    assert any("SOME_NEW_TYPE" in c.cause for c in output.probable_causes)


def test_fallback_raises_clearly_when_knowledge_base_empty():
    packet = make_packet(knowledge=[], allowed_chunk_ids=[])
    with pytest.raises(RuntimeError, match="knowledge"):
        build_fallback(packet, "TIMEOUT")
