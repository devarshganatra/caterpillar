"""
Cold worker (Stage 3 Batch 3H): consumes incidents:work and turns every
opened/escalated incident into either a Groq-generated, grounded
explanation or a clearly-labelled deterministic fallback. A Groq failure
must NEVER prevent an incident from having an explanation — this worker's
entire job is to guarantee that.

Nothing safety-relevant is imported here, and nothing here is imported by
hot/warm/correlator — this path is fully isolated (see
tests/integration/test_cold_worker.py's grep-based check).

Run: PYTHONPATH=. python -m backend.app.worker.cold
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import IncidentRow, IncidentTimeline, IncidentExplanationRow
from backend.app.genai.client import generate_explanation, LLMUnavailable
from backend.app.genai.fallback import build_fallback
from backend.app.genai.packet import build_incident_packet, packet_hash
from backend.app.genai.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt, build_retry_prompt
from backend.app.genai.schemas import IncidentExplanationLLM
from backend.app.genai.validator import parse_and_validate
from backend.app.knowledge.retriever import KnowledgeRetriever
from contracts.events import UiPush, UiPushType

logger = logging.getLogger(__name__)

GROUP_NAME = "cold-worker"
STREAM_KEY = "incidents:work"
CONSUMER_NAME = "cold-1"  # single consumer: respects the LLM API's rate limits


class ColdWorker:
    def __init__(self, redis_client: Redis, retriever: KnowledgeRetriever | None = None):
        self.redis = redis_client
        self.retriever = retriever or KnowledgeRetriever.from_directory(settings.knowledge_dir)
        self.fallback_count = 0
        self.retry_count = 0

    async def process(self, incident_id: str, reason: str) -> str:
        """
        Returns the outcome for logging/tests: CACHED | GROQ | FALLBACK | FAILED.
        Always tries to leave the incident with SOME explanation, even on
        unexpected exceptions — a Groq outage must never leave an incident
        unexplained.
        """
        t0 = time.monotonic()
        iid = uuid.UUID(incident_id)

        try:
            async with AsyncSessionLocal() as session:
                packet = await build_incident_packet(session, self.redis, iid, self.retriever)
                h = packet_hash(packet)

                existing = await session.execute(
                    select(IncidentExplanationRow).where(
                        IncidentExplanationRow.incident_id == iid, IncidentExplanationRow.packet_hash == h,
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    logger.info(f"cold: CACHED incident_id={incident_id} packet_hash={h[:12]}")
                    return "CACHED"

                output, source, fallback_reason, grounding, retry_count = await self._get_explanation(packet)
                self.retry_count += retry_count
                if source == "FALLBACK":
                    self.fallback_count += 1

                latency_ms = round((time.monotonic() - t0) * 1000)
                await self._persist(session, iid, h, output, source, fallback_reason, grounding, latency_ms)
                await session.commit()
        except Exception as e:
            logger.error(f"cold: unexpected error processing incident_id={incident_id}: {type(e).__name__}: {e}")
            return await self._emergency_fallback(incident_id, reason, str(e))

        await self._publish(incident_id, source)
        await self._update_worker_status(latency_ms)
        logger.info(json.dumps({
            "service": "cold", "incident_id": incident_id, "gemini_status": source,
            "fallback_reason": fallback_reason, "retry_count": retry_count, "latency_ms": latency_ms,
        }))
        return source

    async def _get_explanation(self, packet: dict):
        """
        Returns (IncidentExplanationLLM, source, fallback_reason, grounding_dict, retry_count).
        source is "GROQ" or "FALLBACK".
        """
        if not settings.groq_api_key:
            output = build_fallback(packet, "NO_API_KEY")
            return output, "FALLBACK", "NO_API_KEY", {"valid": False, "violations": [], "attempts": 0}, 0

        attempts = 0
        violations: list[str] = []
        user_prompt = build_user_prompt(packet)

        for attempt_num in range(settings.groq_max_retries + 1):
            attempts += 1
            try:
                raw, meta = await generate_explanation(SYSTEM_PROMPT, user_prompt)
            except LLMUnavailable as e:
                output = build_fallback(packet, e.reason)
                return output, "FALLBACK", e.reason, {"valid": False, "violations": violations, "attempts": attempts}, attempts - 1

            parsed, violations = parse_and_validate(raw, packet)
            if parsed is not None:
                return parsed, "GROQ", None, {"valid": True, "violations": [], "attempts": attempts}, attempts - 1

            user_prompt = build_retry_prompt(packet, violations)

        output = build_fallback(packet, "GROUNDING_INVALID")
        return output, "FALLBACK", "GROUNDING_INVALID", {"valid": False, "violations": violations, "attempts": attempts}, attempts - 1

    async def _persist(self, session: AsyncSession, incident_id: uuid.UUID, packet_hash_: str,
                        output: IncidentExplanationLLM, source: str, fallback_reason: str | None,
                        grounding: dict, latency_ms: int) -> None:
        stmt = pg_insert(IncidentExplanationRow).values(
            id=uuid.uuid4(), incident_id=incident_id, packet_hash=packet_hash_, source=source,
            model_name=settings.groq_model if source == "GROQ" else None, prompt_version=PROMPT_VERSION,
            summary=output.summary, probable_causes=[c.model_dump() for c in output.probable_causes],
            recommended_actions=[a.model_dump() for a in output.recommended_actions],
            lesson=output.lesson.model_dump(), training_refs=output.training_refs,
            confidence=output.confidence, grounding=grounding, fallback_reason=fallback_reason,
            latency_ms=latency_ms,
        ).on_conflict_do_nothing(constraint="uq_incident_explanations_incident_packet")
        await session.execute(stmt)

        explanation_status = "READY" if source == "GROQ" else "FALLBACK"
        await session.execute(
            IncidentRow.__table__.update().where(IncidentRow.id == incident_id).values(
                explanation_status=explanation_status,
            )
        )
        now = datetime.now(timezone.utc)
        summary_text = (
            f"Explanation generated ({settings.groq_model})" if source == "GROQ"
            else f"Explanation generated (fallback: {fallback_reason})"
        )
        await session.execute(pg_insert(IncidentTimeline).values(
            incident_id=incident_id, kind="EXPLANATION", entry_key=f"explanation:{now.isoformat()}",
            event_type=None, severity=None, first_ts=now, last_ts=now, count=1,
            representative_event_id=None, summary=summary_text, actor_id=None,
        ).on_conflict_do_nothing())

    async def _emergency_fallback(self, incident_id: str, reason: str, error_detail: str) -> str:
        """
        Best-effort path when `process()`'s main try block raised something
        unexpected (e.g. a transient DB error mid-packet-build). Tries once
        more, cleanly, to at least mark the incident FAILED so it's visible
        that something needs attention — never silently drops it.
        """
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    IncidentRow.__table__.update().where(IncidentRow.id == uuid.UUID(incident_id)).values(
                        explanation_status="FAILED",
                    )
                )
                await session.commit()
            logger.error(f"cold: marked incident_id={incident_id} explanation_status=FAILED ({error_detail})")
            return "FAILED"
        except Exception as e:
            # DB itself is unreachable: leave the incidents:work message
            # unacked so it's retried later, once the DB recovers.
            logger.error(f"cold: could not even mark FAILED for incident_id={incident_id}: {e}")
            raise

    async def _publish(self, incident_id: str, source: str) -> None:
        async with AsyncSessionLocal() as session:
            row = (await session.execute(select(IncidentRow).where(IncidentRow.id == uuid.UUID(incident_id)))).scalar_one_or_none()
        if row is None:
            return
        try:
            push = UiPush(
                type=UiPushType.incident, machine_id=row.machine_id, ts=datetime.now(timezone.utc),
                payload={"action": "explained", "incident_id": incident_id, "source": source},
            )
            await self.redis.publish(f"ui:{row.machine_id}", push.model_dump_json())
        except Exception as e:
            logger.error(f"cold: failed to publish UI push for incident_id={incident_id}: {e}")

    async def _update_worker_status(self, latency_ms: int) -> None:
        try:
            await self.redis.hset("worker:status:cold", mapping={
                "last_ok_ts": datetime.now(timezone.utc).isoformat(),
                "last_latency_ms": str(latency_ms),
                "fallback_count": str(self.fallback_count),
                "retry_count": str(self.retry_count),
            })
        except Exception:
            pass


# ---------------------------------------------------------------------- #
# main() — single-consumer loop
# ---------------------------------------------------------------------- #

async def _init_consumer_group(redis_client: Redis) -> None:
    try:
        await redis_client.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def main():
    logging.basicConfig(level=settings.log_level)
    logger.info("Starting cold worker...")
    redis_client = Redis.from_url(settings.redis_url)
    await redis_client.ping()

    worker = ColdWorker(redis_client)
    await _init_consumer_group(redis_client)

    try:
        pending = await redis_client.xreadgroup(GROUP_NAME, CONSUMER_NAME, {STREAM_KEY: "0-0"}, count=50)
        for _, messages in pending:
            for msg_id, data in messages:
                await _process_message(worker, redis_client, msg_id, data)
    except Exception as e:
        logger.error(f"cold: error replaying pending: {e}")

    backoff = 1.0
    while True:
        try:
            msgs = await redis_client.xreadgroup(GROUP_NAME, CONSUMER_NAME, {STREAM_KEY: ">"}, count=10, block=2000)
            if not msgs:
                continue
            for _, messages in msgs:
                for msg_id, data in messages:
                    await _process_message(worker, redis_client, msg_id, data)
            backoff = 1.0
        except Exception as e:
            logger.error(f"cold: consumer loop error: {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


async def _process_message(worker: ColdWorker, redis_client: Redis, msg_id: bytes, data: dict) -> None:
    incident_id = data.get(b"incident_id", b"").decode()
    reason = data.get(b"reason", b"").decode()
    if not incident_id:
        await redis_client.xack(STREAM_KEY, GROUP_NAME, msg_id)
        return
    try:
        await worker.process(incident_id, reason)
    except Exception as e:
        logger.error(f"cold: process() raised for incident_id={incident_id}, leaving pending: {e}")
        return  # no XACK: retried on next read/restart
    await redis_client.xack(STREAM_KEY, GROUP_NAME, msg_id)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
