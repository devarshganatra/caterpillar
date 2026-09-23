"""
Integration-flavored tests for backend.app.genai.packet against a real DB
(needs docker-compose up) — the packet builder queries incidents, timeline,
events, windows, tasks, and ETA, so a meaningful test needs real rows.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import IncidentRow, IncidentTimeline, Event as DBEvent
from backend.app.genai.packet import build_incident_packet, packet_hash
from backend.app.knowledge.retriever import KnowledgeRetriever

pytestmark = pytest.mark.integration

MACHINE = "EXC001"
SITE = "SITE-A"


@pytest.fixture
def retriever():
    return KnowledgeRetriever.from_directory(settings.knowledge_dir)


@pytest.fixture
async def redis_client():
    r = Redis.from_url(settings.redis_url)
    yield r
    await r.aclose()


async def _seed_incident_with_events(n_events: int = 3) -> uuid.UUID:
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
                frame_seq=int(now.timestamp()) + i,
            ))
        trigger_id = event_ids[0]
        session.add(IncidentRow(
            id=incident_id, machine_id=MACHINE, site_id=SITE, operator_id="OP-TEST", task_id=None,
            category="SEATBELT_VIOLATION", severity="CRITICAL", escalated=False, status="OPEN",
            opened_at=now, last_event_at=now + timedelta(seconds=n_events - 1),
            trigger_event_id=trigger_id, event_count=n_events, risk_level_at_open="NORMAL",
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
    return incident_id


async def _cleanup(incident_id: uuid.UUID):
    async with AsyncSessionLocal() as session:
        row = await session.get(IncidentRow, incident_id)
        if row is None:
            return
        trigger_id = row.trigger_event_id
        events_result = await session.execute(
            IncidentTimeline.__table__.select().where(IncidentTimeline.incident_id == incident_id)
        )
        event_ids = [r.representative_event_id for r in events_result.fetchall() if r.representative_event_id]
        await session.execute(delete(IncidentTimeline).where(IncidentTimeline.incident_id == incident_id))
        await session.execute(delete(IncidentRow).where(IncidentRow.id == incident_id))
        for eid in set(event_ids + [trigger_id]):
            await session.execute(delete(DBEvent).where(DBEvent.id == eid))
        await session.commit()


@pytest.mark.asyncio
async def test_packet_hash_deterministic_for_same_db_state(redis_client, retriever):
    incident_id = await _seed_incident_with_events()
    try:
        async with AsyncSessionLocal() as session:
            p1 = await build_incident_packet(session, redis_client, incident_id, retriever)
        async with AsyncSessionLocal() as session:
            p2 = await build_incident_packet(session, redis_client, incident_id, retriever)
        assert packet_hash(p1) == packet_hash(p2)
    finally:
        await _cleanup(incident_id)


@pytest.mark.asyncio
async def test_allowed_event_ids_matches_representative_events(redis_client, retriever):
    incident_id = await _seed_incident_with_events(n_events=3)
    try:
        async with AsyncSessionLocal() as session:
            packet = await build_incident_packet(session, redis_client, incident_id, retriever)
        assert set(packet["allowed_event_ids"]) == {e["event_id"] for e in packet["events"]}
        assert len(packet["allowed_event_ids"]) == 3
    finally:
        await _cleanup(incident_id)


@pytest.mark.asyncio
async def test_timeline_over_max_is_truncated(redis_client, retriever, monkeypatch):
    monkeypatch.setattr(settings, "packet_max_timeline", 5)
    incident_id = await _seed_incident_with_events(n_events=8)
    try:
        async with AsyncSessionLocal() as session:
            packet = await build_incident_packet(session, redis_client, incident_id, retriever)
        assert packet["timeline_truncated"] is True
        assert len(packet["timeline"]) <= 5
    finally:
        await _cleanup(incident_id)


@pytest.mark.asyncio
async def test_evidence_whitelist_strips_unknown_fields(redis_client, retriever):
    incident_id = await _seed_incident_with_events(n_events=1)
    try:
        async with AsyncSessionLocal() as session:
            packet = await build_incident_packet(session, redis_client, incident_id, retriever)
        evidence = packet["events"][0]["evidence"]
        assert set(evidence.keys()) <= {"state"}  # SEATBELT_VIOLATION whitelist per packet.py
    finally:
        await _cleanup(incident_id)


@pytest.mark.asyncio
async def test_operator_identity_is_opaque_id_only(redis_client, retriever):
    incident_id = await _seed_incident_with_events(n_events=1)
    try:
        async with AsyncSessionLocal() as session:
            packet = await build_incident_packet(session, redis_client, incident_id, retriever)
        assert packet["operator_id"] == "OP-TEST"
        assert "operator_name" not in packet
    finally:
        await _cleanup(incident_id)


@pytest.mark.asyncio
async def test_knowledge_refs_come_from_retriever(redis_client, retriever):
    incident_id = await _seed_incident_with_events(n_events=2)
    try:
        async with AsyncSessionLocal() as session:
            packet = await build_incident_packet(session, redis_client, incident_id, retriever)
        assert len(packet["knowledge"]) > 0
        assert packet["knowledge"][0]["chunk_id"] in packet["allowed_chunk_ids"]
        # a seatbelt-heavy incident should surface at least one seatbelt-safety chunk
        assert any(k["chunk_id"].startswith("seatbelt-safety") for k in packet["knowledge"])
    finally:
        await _cleanup(incident_id)
