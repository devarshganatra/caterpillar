"""
One-off manual verification script for the cold-path LLM call (Stage 3
Batch 3G). Builds a real Incident Packet for a given incident, calls the
real Groq API, and prints the validated structured output. Never prints
the API key.

Usage:
    PYTHONPATH=. venv/bin/python scripts/try_llm.py <incident_id>
"""
import asyncio
import json
import sys

from redis.asyncio import Redis

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.genai.client import generate_explanation, LLMUnavailable
from backend.app.genai.packet import build_incident_packet, packet_hash
from backend.app.genai.prompts import SYSTEM_PROMPT, build_user_prompt
from backend.app.genai.schemas import IncidentExplanationLLM
from backend.app.knowledge.retriever import KnowledgeRetriever


async def main(incident_id: str):
    if not settings.groq_api_key:
        print("GROQ_API_KEY is not set in the environment/.env — nothing to try.", file=sys.stderr)
        return

    retriever = KnowledgeRetriever.from_directory(settings.knowledge_dir)
    redis_client = Redis.from_url(settings.redis_url)

    async with AsyncSessionLocal() as session:
        packet = await build_incident_packet(session, redis_client, incident_id, retriever)

    print(f"Packet hash: {packet_hash(packet)}")
    print(f"allowed_event_ids: {packet['allowed_event_ids']}")
    print(f"allowed_chunk_ids: {packet['allowed_chunk_ids']}")
    print(f"Calling Groq model={settings.groq_model} ...")

    try:
        raw, meta = await generate_explanation(SYSTEM_PROMPT, build_user_prompt(packet))
    except LLMUnavailable as e:
        print(f"LLM unavailable: {e.reason} ({e.detail})", file=sys.stderr)
        return
    finally:
        await redis_client.aclose()

    print(f"model={meta['model']} latency_ms={meta['latency_ms']} usage={meta['usage']}")
    parsed = IncidentExplanationLLM.model_validate(raw)
    print(json.dumps(parsed.model_dump(), indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: PYTHONPATH=. venv/bin/python scripts/try_llm.py <incident_id>", file=sys.stderr)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
