"""Grounding validation for Stage 4 lessons — same rules as incident
explanations (genai/validator.py), scoped to LessonLLM's smaller field set.
Reuses the same smuggled-ref/forbidden-phrase detection so the two
validators can never quietly drift apart on what counts as a violation."""
from __future__ import annotations

from pydantic import ValidationError

from backend.app.genai.lesson_schemas import LessonLLM
from backend.app.genai.validator import _check_forbidden_phrases, _check_smuggled_refs


def validate_lesson_grounding(output: LessonLLM, packet: dict) -> list[str]:
    violations: list[str] = []
    allowed_event_ids = set(packet.get("allowed_event_ids", []))
    allowed_chunk_ids = set(packet.get("allowed_chunk_ids", []))

    for ref in output.knowledge_refs:
        if ref not in allowed_chunk_ids:
            violations.append(f"knowledge_refs: unknown chunk id '{ref}'")

    violations += _check_smuggled_refs(output.explanation, allowed_event_ids, allowed_chunk_ids, "explanation")
    violations += _check_smuggled_refs(output.short_tip, allowed_event_ids, allowed_chunk_ids, "short_tip")
    violations += _check_forbidden_phrases(output.explanation, "explanation")

    return violations


def parse_and_validate_lesson(raw: dict, packet: dict) -> tuple[LessonLLM | None, list[str]]:
    """Schema validation first (pydantic), then grounding validation. Schema
    errors are prefixed 'schema:' so the caller can tell the two apart."""
    try:
        parsed = LessonLLM.model_validate(raw)
    except ValidationError as e:
        return None, [f"schema: {err['msg']} at {'.'.join(str(p) for p in err['loc'])}" for err in e.errors()]

    violations = validate_lesson_grounding(parsed, packet)
    if violations:
        return None, violations
    return parsed, []
