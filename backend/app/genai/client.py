"""
ALL LLM calls live here and ONLY here.

Uses Groq's OpenAI-compatible chat completions API (model:
settings.groq_model, currently "openai/gpt-oss-20b" — never hardcode the
model name anywhere else). Architecture decision recorded in PROGRESS.md
"Batch 3G": this project originally scoped Gemini via google-genai, but
switched to Groq (an available API key, with rate limits well suited to
this incident-explanation volume) partway through Stage 3. The contract
this module presents (generate_explanation(system, user) -> (dict, meta))
is provider-agnostic by design, so nothing above this module needed to
change.

No tool calling: per the phase requirement, the Incident Packet (built by
genai.packet) is the ONLY input the model receives — it has no ability to
query anything else.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from groq import AsyncGroq

from backend.app.config import settings
from backend.app.genai.schemas import llm_json_schema
from backend.app.genai.lesson_schemas import lesson_json_schema

logger = logging.getLogger(__name__)

_client: AsyncGroq | None = None


class LLMUnavailable(Exception):
    """Raised whenever the cold worker should fall back to the deterministic
    template instead of a Groq-generated explanation."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason  # NO_API_KEY | TIMEOUT | API_ERROR | MALFORMED
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=settings.groq_api_key)
    return _client


async def _structured_completion(system: str, user: str, schema: dict, schema_name: str) -> tuple[dict, dict]:
    """
    Shared call path for every JSON-schema-constrained structured output
    request this project makes (incident explanations, Stage 4 lessons).
    Returns (parsed_json_dict, meta) where meta = {model, latency_ms, usage}.
    Never logs the API key or the full prompt — only the packet hash (the
    caller's responsibility) and this call's outcome.
    """
    if not settings.groq_api_key:
        raise LLMUnavailable("NO_API_KEY")

    client = _get_client()
    t0 = time.monotonic()

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.groq_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                },
                temperature=settings.groq_temperature,
            ),
            timeout=settings.groq_timeout_s,
        )
    except TimeoutError:
        raise LLMUnavailable("TIMEOUT", f"exceeded {settings.groq_timeout_s}s")
    except Exception as e:
        # Covers groq.APIError / APIConnectionError / RateLimitError / etc.
        # without importing every specific exception class — any transport
        # or API failure degrades to the deterministic fallback the same way.
        raise LLMUnavailable("API_ERROR", type(e).__name__)

    latency_ms = round((time.monotonic() - t0) * 1000, 1)
    raw_text = response.choices[0].message.content if response.choices else None
    if not raw_text:
        raise LLMUnavailable("MALFORMED", "empty response content")

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise LLMUnavailable("MALFORMED", f"non-JSON response: {e}")

    meta = {
        "model": getattr(response, "model", settings.groq_model),
        "latency_ms": latency_ms,
        "usage": response.usage.model_dump() if getattr(response, "usage", None) else None,
    }
    return parsed, meta


async def generate_explanation(system: str, user: str) -> tuple[dict, dict]:
    """Calls the LLM for an incident explanation (see schemas.py)."""
    return await _structured_completion(system, user, llm_json_schema(), "incident_explanation")


async def generate_lesson_content(system: str, user: str) -> tuple[dict, dict]:
    """Calls the LLM for a Stage 4 operator lesson (see lesson_schemas.py)."""
    return await _structured_completion(system, user, lesson_json_schema(), "operator_lesson")
