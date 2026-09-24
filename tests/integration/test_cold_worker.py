"""
Integration tests for the cold worker's full process() pipeline — real
Postgres + Redis (docker-compose up), Groq always mocked (no network in
automated tests; scripts/try_llm.py is the manual real-API check).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, select

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import (
    IncidentRow, IncidentTimeline, IncidentExplanationRow, LessonRow, Event as DBEvent,
)
from backend.app.worker.cold import ColdWorker
from backend.app.genai.client import LLMUnavailable
from backend.app.knowledge.retriever import KnowledgeRetriever

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


async def _seed_incident(n_events: int = 2) -> tuple[uuid.UUID, list[uuid.UUID]]:
    now = datetime.now(timezone.utc)
    incident_id = uuid.uuid4()
    event_ids = []
    async with AsyncSessionLocal() as session:
        for i in range(n_events):
            eid = uuid.uuid4()
            event_ids.append(eid)
            session.add(DBEvent(
                id=eid, type="SEATBELT_VIOLATION", severity="CRITICAL", machine_id=MACHINE,
                operator_id="OP-TEST", site_id=SITE, ts=now + timedelta(seconds=i),
                source_engine="test@1.0", evidence={"state": "WORKING"},
                frame_seq=int(now.timestamp()) + i,  # seconds-epoch fits int32; *1000 (ms) does not
            ))
        session.add(IncidentRow(
            id=incident_id, machine_id=MACHINE, site_id=SITE, operator_id="OP-TEST", task_id=None,
            category="SEATBELT_VIOLATION", severity="CRITICAL", escalated=False, status="OPEN",
            opened_at=now, last_event_at=now + timedelta(seconds=n_events - 1),
            trigger_event_id=event_ids[0], event_count=n_events, risk_level_at_open="NORMAL",
            explanation_status="PENDING",
        ))
        for i, eid in enumerate(event_ids):
            session.add(IncidentTimeline(
                incident_id=incident_id, kind="EVENT", entry_key=f"SEATBELT_VIOLATION:{i}",
                event_type="SEATBELT_VIOLATION", severity="CRITICAL",
                first_ts=now + timedelta(seconds=i), last_ts=now + timedelta(seconds=i),
                count=1, representative_event_id=eid, summary=f"Seatbelt unfastened while WORKING ({i})",
            ))
        await session.commit()
    return incident_id, event_ids


async def _cleanup(incident_id: uuid.UUID):
    async with AsyncSessionLocal() as session:
        row = await session.get(IncidentRow, incident_id)
        if row is None:
            return
        tl = await session.execute(
            select(IncidentTimeline.representative_event_id).where(IncidentTimeline.incident_id == incident_id)
        )
        event_ids = [r for r in tl.scalars().all() if r is not None]
        # Stage 4A: ColdWorker.process() now also generates a lesson (for
        # any incident with an operator_id) as a side effect of persisting
        # the explanation — that FK-references this incident too.
        await session.execute(delete(LessonRow).where(LessonRow.incident_id == incident_id))
        await session.execute(delete(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == incident_id))
        await session.execute(delete(IncidentTimeline).where(IncidentTimeline.incident_id == incident_id))
        await session.execute(delete(IncidentRow).where(IncidentRow.id == incident_id))
        for eid in set(event_ids + [row.trigger_event_id]):
            await session.execute(delete(DBEvent).where(DBEvent.id == eid))
        await session.commit()


def _valid_llm_json(event_id: uuid.UUID) -> dict:
    return {
        "summary": "Seatbelt was unfastened while the machine was working.",
        "probable_causes": [{"cause": "Operator did not refasten the seatbelt.", "evidence_refs": [str(event_id)], "likelihood": "HIGH"}],
        "recommended_actions": [{"action": "Refasten the seatbelt.", "rationale": "Prevents ejection.", "knowledge_refs": ["seatbelt-safety#overview"]}],
        "lesson": {"title": "Seatbelt compliance", "tip": "Always fasten before moving.", "knowledge_refs": ["seatbelt-safety#overview"]},
        "training_refs": ["seatbelt-safety#overview"],
        "confidence": "HIGH",
    }


@pytest.mark.asyncio
async def test_no_api_key_gives_fallback(redis_client, retriever, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    incident_id, event_ids = await _seed_incident()
    try:
        worker = ColdWorker(redis_client, retriever)
        outcome = await worker.process(str(incident_id), "opened")
        assert outcome == "FALLBACK"

        async with AsyncSessionLocal() as session:
            row = await session.get(IncidentRow, incident_id)
            exp = (await session.execute(
                select(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == incident_id)
            )).scalar_one()
        assert row.explanation_status == "FALLBACK"
        assert exp.source == "FALLBACK"
        assert exp.fallback_reason == "NO_API_KEY"
    finally:
        await _cleanup(incident_id)


@pytest.mark.asyncio
async def test_invalid_then_valid_response_succeeds_with_two_attempts(redis_client, retriever, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    incident_id, event_ids = await _seed_incident()
    try:
        invalid = {"summary": "too short"}  # fails schema
        valid = _valid_llm_json(event_ids[0])
        call_results = [(invalid, {"model": "m", "latency_ms": 1, "usage": None}),
                         (valid, {"model": "m", "latency_ms": 1, "usage": None})]

        async def fake_generate(system, user):
            return call_results.pop(0)

        with patch("backend.app.worker.cold.generate_explanation", side_effect=fake_generate):
            worker = ColdWorker(redis_client, retriever)
            outcome = await worker.process(str(incident_id), "opened")

        assert outcome == "GROQ"
        async with AsyncSessionLocal() as session:
            exp = (await session.execute(
                select(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == incident_id)
            )).scalar_one()
        assert exp.source == "GROQ"
        assert exp.grounding["attempts"] == 2
    finally:
        await _cleanup(incident_id)


@pytest.mark.asyncio
async def test_invalid_twice_falls_back_with_grounding_invalid(redis_client, retriever, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    incident_id, event_ids = await _seed_incident()
    try:
        invalid = {"summary": "too short"}

        async def fake_generate(system, user):
            return invalid, {"model": "m", "latency_ms": 1, "usage": None}

        with patch("backend.app.worker.cold.generate_explanation", side_effect=fake_generate):
            worker = ColdWorker(redis_client, retriever)
            outcome = await worker.process(str(incident_id), "opened")

        assert outcome == "FALLBACK"
        async with AsyncSessionLocal() as session:
            exp = (await session.execute(
                select(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == incident_id)
            )).scalar_one()
        assert exp.fallback_reason == "GROUNDING_INVALID"
    finally:
        await _cleanup(incident_id)


@pytest.mark.asyncio
async def test_timeout_falls_back(redis_client, retriever, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    incident_id, event_ids = await _seed_incident()
    try:
        async def fake_generate(system, user):
            raise LLMUnavailable("TIMEOUT", "exceeded 8.0s")

        with patch("backend.app.worker.cold.generate_explanation", side_effect=fake_generate):
            worker = ColdWorker(redis_client, retriever)
            outcome = await worker.process(str(incident_id), "opened")

        assert outcome == "FALLBACK"
        async with AsyncSessionLocal() as session:
            exp = (await session.execute(
                select(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == incident_id)
            )).scalar_one()
        assert exp.fallback_reason == "TIMEOUT"
    finally:
        await _cleanup(incident_id)


@pytest.mark.asyncio
async def test_malformed_response_falls_back(redis_client, retriever, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    incident_id, event_ids = await _seed_incident()
    try:
        async def fake_generate(system, user):
            raise LLMUnavailable("MALFORMED", "non-JSON")

        with patch("backend.app.worker.cold.generate_explanation", side_effect=fake_generate):
            worker = ColdWorker(redis_client, retriever)
            outcome = await worker.process(str(incident_id), "opened")

        assert outcome == "FALLBACK"
        async with AsyncSessionLocal() as session:
            exp = (await session.execute(
                select(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == incident_id)
            )).scalar_one()
        assert exp.fallback_reason == "MALFORMED"
    finally:
        await _cleanup(incident_id)


@pytest.mark.asyncio
async def test_same_packet_processed_twice_is_cached(redis_client, retriever, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    incident_id, event_ids = await _seed_incident()
    try:
        valid = _valid_llm_json(event_ids[0])
        mock_generate = AsyncMock(return_value=(valid, {"model": "m", "latency_ms": 1, "usage": None}))

        with patch("backend.app.worker.cold.generate_explanation", mock_generate):
            worker = ColdWorker(redis_client, retriever)
            first = await worker.process(str(incident_id), "opened")
            second = await worker.process(str(incident_id), "opened")  # same DB state -> same packet_hash

        assert first == "GROQ"
        assert second == "CACHED"
        assert mock_generate.await_count == 1  # the LLM was called exactly once

        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == incident_id)
            )).scalars().all()
        assert len(rows) == 1
    finally:
        await _cleanup(incident_id)


@pytest.mark.asyncio
async def test_persistent_llm_failure_never_leaves_incident_without_explanation(redis_client, retriever, monkeypatch):
    """Failure isolation: even if the LLM raises on every call, the incident still ends up explained (via fallback)."""
    monkeypatch.setattr(settings, "groq_api_key", "fake-key")
    incident_id, event_ids = await _seed_incident()
    try:
        async def always_fails(system, user):
            raise LLMUnavailable("API_ERROR", "boom")

        with patch("backend.app.worker.cold.generate_explanation", side_effect=always_fails):
            worker = ColdWorker(redis_client, retriever)
            outcome = await worker.process(str(incident_id), "opened")

        assert outcome == "FALLBACK"
        async with AsyncSessionLocal() as session:
            row = await session.get(IncidentRow, incident_id)
        assert row.explanation_status == "FALLBACK"  # never PENDING, never stuck
    finally:
        await _cleanup(incident_id)


def test_cold_worker_not_imported_by_hot_warm_correlator():
    """Nothing in the safety-critical path imports the cold worker."""
    import ast
    import inspect
    for module_path in (
        "backend.app.worker.hot", "backend.app.worker.warm", "backend.app.worker.correlator",
        "backend.app.worker.entrypoint", "core.copilot_core.state", "core.copilot_core.safety",
        "core.copilot_core.arbitrator", "core.copilot_core.risk",
    ):
        module = __import__(module_path, fromlist=["_"])
        tree = ast.parse(inspect.getsource(module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert not any("cold" in name or "genai" in name for name in imported), (module_path, imported)
