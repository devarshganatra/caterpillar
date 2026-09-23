"""
Grounding validation for LLM-generated incident explanations (Stage 3
Batch 3H). This is the mandatory second gate after schema validation: an
output can be perfectly schema-shaped and still cite an event or knowledge
chunk that doesn't exist in the packet it was given. Nothing here trusts
the model to have followed the prompt's rules — every reference is checked
against the packet's own allowed sets.
"""
from __future__ import annotations

import re

from pydantic import ValidationError

from backend.app.genai.schemas import IncidentExplanationLLM

# UUID (event ids) or "<doc>#<heading>" (chunk ids) shaped tokens hidden
# inside free text — catches an id that was never put in a *_refs list but
# was smuggled into prose instead.
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_CHUNK_RE = re.compile(r"\b[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*\b")

_FORBIDDEN_PHRASES = ["proven", "definitely caused", "root cause is"]


def _check_forbidden_phrases(text: str, field_name: str) -> list[str]:
    lowered = text.lower()
    return [f"forbidden_claim: '{phrase}' found in {field_name}" for phrase in _FORBIDDEN_PHRASES if phrase in lowered]


def _check_smuggled_refs(text: str, allowed_event_ids: set[str], allowed_chunk_ids: set[str], field_name: str) -> list[str]:
    violations = []
    for match in _UUID_RE.findall(text):
        if match not in allowed_event_ids:
            violations.append(f"smuggled_ref: unknown event id '{match}' in free text field {field_name}")
    for match in _CHUNK_RE.findall(text):
        if match not in allowed_chunk_ids:
            violations.append(f"smuggled_ref: unknown chunk id '{match}' in free text field {field_name}")
    return violations


def validate_grounding(output: IncidentExplanationLLM, packet: dict) -> list[str]:
    """Returns a list of violation strings; empty list means valid."""
    violations: list[str] = []
    allowed_event_ids = set(packet.get("allowed_event_ids", []))
    allowed_chunk_ids = set(packet.get("allowed_chunk_ids", []))

    # 1 + 3: probable_causes.evidence_refs must exist and be non-empty
    # (non-empty is also schema-enforced by Field(min_length=1), but a
    # belt-and-braces check here costs nothing).
    for i, cause in enumerate(output.probable_causes):
        if not cause.evidence_refs:
            violations.append(f"probable_causes[{i}]: no evidence_refs")
        for ref in cause.evidence_refs:
            if ref not in allowed_event_ids:
                violations.append(f"probable_causes[{i}].evidence_refs: unknown event id '{ref}'")

    # 2: knowledge/training refs must exist
    for i, action in enumerate(output.recommended_actions):
        for ref in action.knowledge_refs:
            if ref not in allowed_chunk_ids:
                violations.append(f"recommended_actions[{i}].knowledge_refs: unknown chunk id '{ref}'")
    for ref in output.lesson.knowledge_refs:
        if ref not in allowed_chunk_ids:
            violations.append(f"lesson.knowledge_refs: unknown chunk id '{ref}'")
    for ref in output.training_refs:
        if ref not in allowed_chunk_ids:
            violations.append(f"training_refs: unknown chunk id '{ref}'")

    # 4: no smuggled refs in free text
    violations += _check_smuggled_refs(output.summary, allowed_event_ids, allowed_chunk_ids, "summary")
    for i, cause in enumerate(output.probable_causes):
        violations += _check_smuggled_refs(cause.cause, allowed_event_ids, allowed_chunk_ids, f"probable_causes[{i}].cause")
    for i, action in enumerate(output.recommended_actions):
        violations += _check_smuggled_refs(action.action, allowed_event_ids, allowed_chunk_ids, f"recommended_actions[{i}].action")
        violations += _check_smuggled_refs(action.rationale, allowed_event_ids, allowed_chunk_ids, f"recommended_actions[{i}].rationale")
    violations += _check_smuggled_refs(output.lesson.tip, allowed_event_ids, allowed_chunk_ids, "lesson.tip")

    # 5: forbidden claim wording
    violations += _check_forbidden_phrases(output.summary, "summary")
    for i, cause in enumerate(output.probable_causes):
        violations += _check_forbidden_phrases(cause.cause, f"probable_causes[{i}].cause")

    return violations


def parse_and_validate(raw: dict, packet: dict) -> tuple[IncidentExplanationLLM | None, list[str]]:
    """Schema validation first (pydantic), then grounding validation. Schema
    errors are prefixed 'schema:' so the caller can tell the two apart."""
    try:
        parsed = IncidentExplanationLLM.model_validate(raw)
    except ValidationError as e:
        return None, [f"schema: {err['msg']} at {'.'.join(str(p) for p in err['loc'])}" for err in e.errors()]

    violations = validate_grounding(parsed, packet)
    if violations:
        return None, violations
    return parsed, []
