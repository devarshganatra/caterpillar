"""Unit tests for backend.app.genai.lesson_validator."""
import pytest

from backend.app.genai.lesson_schemas import LessonLLM
from backend.app.genai.lesson_validator import validate_lesson_grounding, parse_and_validate_lesson

pytestmark = pytest.mark.unit

PACKET = {
    "allowed_event_ids": ["evt-1", "evt-2"],
    "allowed_chunk_ids": ["seatbelt-safety#overview"],
}

VALID_RAW = {
    "title": "Seatbelt Compliance",
    "short_tip": "Always fasten your seatbelt before the machine moves.",
    "explanation": "This incident happened because the seatbelt was unfastened while the machine was working, which removes your main protection if the machine tips or stops suddenly.",
    "knowledge_refs": ["seatbelt-safety#overview"],
}


def _valid() -> LessonLLM:
    return LessonLLM.model_validate(VALID_RAW)


def test_fully_valid_lesson_passes():
    assert validate_lesson_grounding(_valid(), PACKET) == []


def test_unknown_chunk_id_rejected():
    raw = dict(VALID_RAW, knowledge_refs=["not-a-real-chunk#nope"])
    output = LessonLLM.model_validate(raw)
    violations = validate_lesson_grounding(output, PACKET)
    assert any("not-a-real-chunk#nope" in v for v in violations)


def test_smuggled_uuid_in_explanation_rejected():
    raw = dict(VALID_RAW, explanation="Related to event 12345678-1234-1234-1234-123456789abc which caused this.")
    output = LessonLLM.model_validate(raw)
    violations = validate_lesson_grounding(output, PACKET)
    assert any("smuggled_ref" in v for v in violations)


def test_smuggled_chunk_ref_in_tip_rejected():
    raw = dict(VALID_RAW, short_tip="See other-doc#some-heading for details.")
    output = LessonLLM.model_validate(raw)
    violations = validate_lesson_grounding(output, PACKET)
    assert any("smuggled_ref" in v for v in violations)


def test_forbidden_phrase_rejected():
    raw = dict(VALID_RAW, explanation="It is proven that the seatbelt caused this incident.")
    output = LessonLLM.model_validate(raw)
    violations = validate_lesson_grounding(output, PACKET)
    assert any("forbidden_claim" in v for v in violations)


def test_parse_and_validate_rejects_schema_violation():
    raw = {"title": "x"}  # missing required fields
    parsed, violations = parse_and_validate_lesson(raw, PACKET)
    assert parsed is None
    assert any(v.startswith("schema:") for v in violations)


def test_parse_and_validate_accepts_valid_grounded_output():
    parsed, violations = parse_and_validate_lesson(VALID_RAW, PACKET)
    assert parsed is not None
    assert violations == []
