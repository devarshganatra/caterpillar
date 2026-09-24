"""
Integration tests for Stage 4A lesson generation — real Postgres + Redis
(docker-compose up), Groq always mocked or absent (no network in automated
tests; scripts/try_llm.py is the manual real-API check pattern used
throughout this project).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, select

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import (
    IncidentRow, IncidentTimeline, IncidentExplanationRow, LessonRow, Event as DBEvent,
)
from backend.app.worker.cold import ColdWorker
from backend.app.knowledge.retriever import KnowledgeRetriever
from contracts.ids import lesson_id as make_lesson_id

pytestmark = pytest.mark.integration

MACHINE = "EXC001"
SITE = "SITE-A"


@pytest.fixture
async def redis_client():
    r = Redis.from_url(settings.redis_url)
    yield r
    await r.aclose()


@pytest.fixture
def retriever():
    return KnowledgeRetriever.from_directory(settings.knowledge_dir)


async def _seed_incident(operator_id: str | None = "OP-TEST") -> tuple[uuid.UUID, uuid.UUID]:
    now = datetime.now(timezone.utc)
    incident_id = uuid.uuid4()
    event_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(DBEvent(
            id=event_id, type="SEATBELT_VIOLATION", severity="CRITICAL", machine_id=MACHINE,
            operator_id=operator_id or "", site_id=SITE, ts=now, source_engine="test@1.0",
            evidence={"state": "WORKING"}, frame_seq=int(now.timestamp()),
        ))
        session.add(IncidentRow(
            id=incident_id, machine_id=MACHINE, site_id=SITE, operator_id=operator_id, task_id=None,
            category="SEATBELT_VIOLATION", severity="CRITICAL", escalated=False, status="OPEN",
            opened_at=now, last_event_at=now, trigger_event_id=event_id, event_count=1,
            risk_level_at_open="NORMAL", explanation_status="PENDING",
        ))
        session.add(IncidentTimeline(
            incident_id=incident_id, kind="EVENT", entry_key="SEATBELT_VIOLATION:0",
            event_type="SEATBELT_VIOLATION", severity="CRITICAL", first_ts=now, last_ts=now,
            count=1, representative_event_id=event_id, summary="Seatbelt unfastened while WORKING",
        ))
        await session.commit()
    return incident_id, event_id


async def _cleanup(incident_id: uuid.UUID, event_id: uuid.UUID):
    async with AsyncSessionLocal() as session:
        await session.execute(delete(LessonRow).where(LessonRow.incident_id == incident_id))
        await session.execute(delete(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == incident_id))
        await session.execute(delete(IncidentTimeline).where(IncidentTimeline.incident_id == incident_id))
        await session.execute(delete(IncidentRow).where(IncidentRow.id == incident_id))
        await session.execute(delete(DBEvent).where(DBEvent.id == event_id))
        await session.commit()


@pytest.mark.asyncio
async def test_lesson_generated_with_fallback_when_no_api_key(redis_client, retriever, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    incident_id, event_id = await _seed_incident()
    try:
        worker = ColdWorker(redis_client, retriever)
        outcome = await worker.process(str(incident_id), "opened")
        assert outcome == "FALLBACK"

        async with AsyncSessionLocal() as session:
            lesson = (await session.execute(
                select(LessonRow).where(LessonRow.incident_id == incident_id)
            )).scalar_one()
        assert lesson.source == "FALLBACK"
        assert lesson.status == "FALLBACK"
        assert lesson.fallback_reason == "NO_API_KEY"
        assert lesson.operator_id == "OP-TEST"
        assert lesson.machine_id == MACHINE
        assert lesson.knowledge_refs  # grounded, non-empty
        assert lesson.delivered_at is None  # not yet delivered to IDLE_HUB
        assert lesson.id == uuid.UUID(make_lesson_id(str(incident_id)))  # deterministic
    finally:
        await _cleanup(incident_id, event_id)


@pytest.mark.asyncio
async def test_lesson_skipped_when_incident_has_no_operator(redis_client, retriever, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    incident_id, event_id = await _seed_incident(operator_id=None)
    try:
        worker = ColdWorker(redis_client, retriever)
        outcome = await worker.process(str(incident_id), "opened")
        assert outcome == "FALLBACK"  # explanation still generated regardless

        async with AsyncSessionLocal() as session:
            lesson = (await session.execute(
                select(LessonRow).where(LessonRow.incident_id == incident_id)
            )).scalar_one_or_none()
        assert lesson is None
    finally:
        await _cleanup(incident_id, event_id)


@pytest.mark.asyncio
async def test_lesson_not_duplicated_on_reprocessing(redis_client, retriever, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    incident_id, event_id = await _seed_incident()
    try:
        worker = ColdWorker(redis_client, retriever)
        await worker.process(str(incident_id), "opened")

        # A second, independent call against the SAME incident state (e.g. a
        # redelivered incidents:work message after a restart) must not
        # create a second lesson row.
        async with AsyncSessionLocal() as session:
            from backend.app.genai.packet import build_incident_packet
            packet = await build_incident_packet(session, redis_client, incident_id, retriever)
            await worker._maybe_generate_lesson(session, incident_id, packet)
            await session.commit()

        async with AsyncSessionLocal() as session:
            count_result = await session.execute(
                select(LessonRow).where(LessonRow.incident_id == incident_id)
            )
            assert len(count_result.scalars().all()) == 1
    finally:
        await _cleanup(incident_id, event_id)


@pytest.mark.asyncio
async def test_lesson_never_breaks_incident_explanation_on_failure(redis_client, retriever, monkeypatch):
    """A lesson-generation exception must never prevent the incident
    explanation itself from being persisted — process() still returns a
    real outcome, not an error."""
    monkeypatch.setattr(settings, "groq_api_key", "")
    incident_id, event_id = await _seed_incident()
    try:
        worker = ColdWorker(redis_client, retriever)
        with patch.object(worker, "_get_lesson", side_effect=RuntimeError("boom")):
            outcome = await worker.process(str(incident_id), "opened")
        assert outcome == "FALLBACK"

        async with AsyncSessionLocal() as session:
            explanation = (await session.execute(
                select(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == incident_id)
            )).scalar_one_or_none()
            lesson = (await session.execute(
                select(LessonRow).where(LessonRow.incident_id == incident_id)
            )).scalar_one_or_none()
        assert explanation is not None  # explanation still persisted
        assert lesson is None  # lesson generation failed cleanly, nothing half-written
    finally:
        await _cleanup(incident_id, event_id)
