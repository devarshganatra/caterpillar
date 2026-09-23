"""
Structured output schema for the cold worker's incident explanation
(Stage 3 Batch 3G). Used BOTH as the JSON-schema constraint passed to the
LLM (via .model_json_schema()) AND to validate/parse whatever comes back —
so "the model returned something schema-shaped" and "we accept it" are
enforced by the exact same definition, not two hand-maintained copies.
"""
from typing import Literal

from pydantic import BaseModel, Field


class ProbableCause(BaseModel):
    cause: str = Field(max_length=300)
    evidence_refs: list[str] = Field(min_length=1, max_length=6)
    likelihood: Literal["LOW", "MEDIUM", "HIGH"]


class RecommendedAction(BaseModel):
    action: str = Field(max_length=200)
    rationale: str = Field(max_length=300)
    knowledge_refs: list[str] = Field(default_factory=list, max_length=4)


class Lesson(BaseModel):
    title: str = Field(max_length=80)
    tip: str = Field(max_length=400)
    knowledge_refs: list[str] = Field(min_length=1, max_length=3)


class IncidentExplanationLLM(BaseModel):
    summary: str = Field(max_length=600)
    probable_causes: list[ProbableCause] = Field(min_length=1, max_length=4)
    recommended_actions: list[RecommendedAction] = Field(min_length=1, max_length=5)
    lesson: Lesson
    training_refs: list[str] = Field(default_factory=list, max_length=5)
    confidence: Literal["LOW", "MEDIUM", "HIGH"]


def llm_json_schema() -> dict:
    """
    A flattened, strict-mode-compatible JSON schema for IncidentExplanationLLM.

    Pydantic's own .model_json_schema() nests sub-models under "$defs" and
    uses "$ref" to point at them, which most structured-output APIs
    (including Groq's OpenAI-compatible json_schema strict mode) either
    reject or silently ignore constraints on. This inlines every $ref by
    hand into one self-contained object schema instead, and sets
    additionalProperties: false at every object level (required for
    "strict" mode to actually reject extra fields).
    """
    # minItems/maxItems/maxLength ARE respected by Groq's strict json_schema
    # mode (verified against the live API) — omitting them (as an earlier
    # version of this schema did) left the model unconstrained on array
    # length even though IncidentExplanationLLM's own Pydantic Field bounds
    # would then reject the response, causing avoidable first-pass
    # validation failures. Every bound here must match the corresponding
    # pydantic Field(...) in this file exactly.
    likelihood_enum = {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]}
    probable_cause = {
        "type": "object",
        "properties": {
            "cause": {"type": "string", "maxLength": 300},
            "evidence_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
            "likelihood": likelihood_enum,
        },
        "required": ["cause", "evidence_refs", "likelihood"],
        "additionalProperties": False,
    }
    recommended_action = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "maxLength": 200},
            "rationale": {"type": "string", "maxLength": 300},
            "knowledge_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        },
        "required": ["action", "rationale", "knowledge_refs"],
        "additionalProperties": False,
    }
    lesson = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 80},
            "tip": {"type": "string", "maxLength": 400},
            "knowledge_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
        },
        "required": ["title", "tip", "knowledge_refs"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "maxLength": 600},
            "probable_causes": {"type": "array", "items": probable_cause, "minItems": 1, "maxItems": 4},
            "recommended_actions": {"type": "array", "items": recommended_action, "minItems": 1, "maxItems": 5},
            "lesson": lesson,
            "training_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "confidence": likelihood_enum,
        },
        "required": ["summary", "probable_causes", "recommended_actions", "lesson", "training_refs", "confidence"],
        "additionalProperties": False,
    }
