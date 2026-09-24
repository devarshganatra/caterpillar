"""
Structured output schema for a Stage 4 operator lesson — same pattern as
genai/schemas.py's IncidentExplanationLLM: one Pydantic model used both to
validate the LLM's response and (via lesson_json_schema()) as the exact
JSON-schema constraint passed to Groq's structured-output mode.
"""
from pydantic import BaseModel, Field


class LessonLLM(BaseModel):
    title: str = Field(max_length=80)
    short_tip: str = Field(max_length=200)
    explanation: str = Field(max_length=800)
    knowledge_refs: list[str] = Field(min_length=1, max_length=3)


def lesson_json_schema() -> dict:
    """Flattened, strict-mode-compatible JSON schema for LessonLLM — see
    schemas.py's llm_json_schema() docstring for why this isn't just
    LessonLLM.model_json_schema()."""
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 80},
            "short_tip": {"type": "string", "maxLength": 200},
            "explanation": {"type": "string", "maxLength": 800},
            "knowledge_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
        },
        "required": ["title", "short_tip", "explanation", "knowledge_refs"],
        "additionalProperties": False,
    }
