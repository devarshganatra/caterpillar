"""Unit tests for backend.app.genai.validator."""
from backend.app.genai.schemas import IncidentExplanationLLM
from backend.app.genai.validator import validate_grounding, parse_and_validate

PACKET = {
    "allowed_event_ids": ["evt-1", "evt-2"],
    "allowed_chunk_ids": ["seatbelt-safety#overview"],
}

VALID_RAW = {
    "summary": "Seatbelt was unfastened while the machine was working.",
    "probable_causes": [{"cause": "Operator did not refasten the seatbelt.", "evidence_refs": ["evt-1"], "likelihood": "HIGH"}],
    "recommended_actions": [{"action": "Refasten the seatbelt.", "rationale": "Prevents ejection.", "knowledge_refs": ["seatbelt-safety#overview"]}],
    "lesson": {"title": "Seatbelt compliance", "tip": "Always fasten before moving.", "knowledge_refs": ["seatbelt-safety#overview"]},
    "training_refs": ["seatbelt-safety#overview"],
    "confidence": "HIGH",
}


def _valid() -> IncidentExplanationLLM:
    return IncidentExplanationLLM.model_validate(VALID_RAW)


def test_fully_valid_output_passes():
    assert validate_grounding(_valid(), PACKET) == []


def test_unknown_event_id_rejected():
    raw = dict(VALID_RAW)
    raw["probable_causes"] = [{"cause": "x", "evidence_refs": ["evt-999"], "likelihood": "HIGH"}]
    output = IncidentExplanationLLM.model_validate(raw)
    violations = validate_grounding(output, PACKET)
    assert any("evt-999" in v for v in violations)


def test_unknown_chunk_id_rejected():
    raw = dict(VALID_RAW)
    raw["recommended_actions"] = [{"action": "x", "rationale": "y", "knowledge_refs": ["not-a-real-chunk#nope"]}]
    output = IncidentExplanationLLM.model_validate(raw)
    violations = validate_grounding(output, PACKET)
    assert any("not-a-real-chunk#nope" in v for v in violations)


def test_smuggled_uuid_in_summary_rejected():
    raw = dict(VALID_RAW)
    raw["summary"] = "Related to event 12345678-1234-1234-1234-123456789abc which caused this."
    output = IncidentExplanationLLM.model_validate(raw)
    violations = validate_grounding(output, PACKET)
    assert any("smuggled_ref" in v for v in violations)


def test_smuggled_chunk_ref_in_cause_rejected():
    raw = dict(VALID_RAW)
    raw["probable_causes"] = [{"cause": "See other-doc#some-heading for details.", "evidence_refs": ["evt-1"], "likelihood": "HIGH"}]
    output = IncidentExplanationLLM.model_validate(raw)
    violations = validate_grounding(output, PACKET)
    assert any("smuggled_ref" in v for v in violations)


def test_root_cause_is_phrase_rejected():
    raw = dict(VALID_RAW)
    raw["summary"] = "The root cause is operator negligence."
    output = IncidentExplanationLLM.model_validate(raw)
    violations = validate_grounding(output, PACKET)
    assert any("forbidden_claim" in v for v in violations)


def test_definitely_caused_phrase_rejected():
    raw = dict(VALID_RAW)
    raw["probable_causes"] = [{"cause": "This definitely caused the incident.", "evidence_refs": ["evt-1"], "likelihood": "HIGH"}]
    output = IncidentExplanationLLM.model_validate(raw)
    violations = validate_grounding(output, PACKET)
    assert any("forbidden_claim" in v for v in violations)


def test_parse_and_validate_schema_error_prefixed():
    parsed, violations = parse_and_validate({"summary": "too short a payload"}, PACKET)
    assert parsed is None
    assert all(v.startswith("schema:") for v in violations)


def test_parse_and_validate_valid_returns_no_violations():
    parsed, violations = parse_and_validate(VALID_RAW, PACKET)
    assert parsed is not None
    assert violations == []


def test_parse_and_validate_grounding_failure_returns_violations_not_schema_prefixed():
    raw = dict(VALID_RAW)
    raw["probable_causes"] = [{"cause": "x", "evidence_refs": ["evt-999"], "likelihood": "HIGH"}]
    parsed, violations = parse_and_validate(raw, PACKET)
    assert parsed is None
    assert violations
    assert not any(v.startswith("schema:") for v in violations)
