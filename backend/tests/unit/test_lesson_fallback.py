"""Unit tests for backend.app.genai.lesson_fallback."""
import pytest

from backend.app.genai.lesson_fallback import build_lesson_fallback
from backend.app.genai.lesson_validator import validate_lesson_grounding

pytestmark = pytest.mark.unit

EVENT_ID_1 = "11111111-1111-1111-1111-111111111111"


def make_packet(**overrides) -> dict:
    packet = {
        "incident": {
            "id": "inc-1", "machine_id": "EXC001", "site_id": "SITE-A", "severity": "CRITICAL",
            "escalated": True, "status": "OPEN", "opened_at": "2026-01-01T00:00:00Z",
            "last_event_at": "2026-01-01T00:05:00Z", "category": "SEATBELT_VIOLATION",
            "trigger_event_id": EVENT_ID_1,
        },
        "knowledge": [
            {"chunk_id": "seatbelt-safety#overview", "title": "Seatbelt Safety", "heading": "Overview",
             "text": "Seatbelts are the single most effective protection. More detail here.",
             "tags": ["SEATBELT_VIOLATION"]},
            {"chunk_id": "proximity-awareness#overview", "title": "Proximity Awareness", "heading": "Overview",
             "text": "Struck-by incidents are a leading cause of injury. More detail here.",
             "tags": ["PROXIMITY_BREACH"]},
        ],
        "allowed_event_ids": [EVENT_ID_1],
        "allowed_chunk_ids": ["seatbelt-safety#overview", "proximity-awareness#overview"],
        "operator_id": "11111111-1111-1111-1111-111111111111",
    }
    packet.update(overrides)
    return packet


def test_lesson_fallback_passes_grounding_validation():
    packet = make_packet()
    output = build_lesson_fallback(packet)
    assert validate_lesson_grounding(output, packet) == []


def test_lesson_fallback_is_deterministic():
    packet = make_packet()
    a = build_lesson_fallback(packet)
    b = build_lesson_fallback(packet)
    assert a.model_dump() == b.model_dump()


def test_lesson_fallback_picks_tag_matched_chunk():
    packet = make_packet()
    output = build_lesson_fallback(packet)
    assert output.knowledge_refs == ["seatbelt-safety#overview"]


def test_lesson_fallback_uses_category_specific_tip():
    output = build_lesson_fallback(make_packet())
    assert "seatbelt" in output.short_tip.lower()


def test_lesson_fallback_unknown_category_uses_default_text():
    packet = make_packet()
    packet["incident"]["category"] = "SOME_NEW_TYPE"
    output = build_lesson_fallback(packet)
    assert output.knowledge_refs  # still grounded, just not tag-matched
    assert output.short_tip and output.explanation


def test_lesson_fallback_raises_clearly_when_knowledge_base_empty():
    packet = make_packet(knowledge=[], allowed_chunk_ids=[])
    with pytest.raises(RuntimeError, match="knowledge"):
        build_lesson_fallback(packet)


def test_lesson_fallback_respects_field_length_bounds():
    output = build_lesson_fallback(make_packet())
    assert len(output.title) <= 80
    assert len(output.short_tip) <= 200
    assert len(output.explanation) <= 800
