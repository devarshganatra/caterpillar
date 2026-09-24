"""
Integration tests for Stage 4B — lesson delivery on the HUD -> IDLE_HUB
transition (backend.app.worker.hot._maybe_deliver_lesson), against real
Postgres + Redis.
"""
import uuid
from datetime import datetime, timezone

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import LessonRow, IncidentRow, Event as DBEvent
from backend.app.worker.hot import MachineHotState, process_frame, IDLE_HUB_DWELL_TICKS

pytestmark = pytest.mark.integration

MACHINE = "EXC001"
OPERATOR = "lesson-delivery-test-operator"


def make_raw_frame(seq: int, ts: datetime, operator_id: str = OPERATOR, **overrides) -> dict:
    base = {
        "seq": str(seq), "ts": ts.isoformat(), "machine_id": MACHINE, "operator_id": operator_id,
        "task_id": "TASK-001", "engine_rpm": "850.0", "engine_temp_c": "70.0",
        "hydraulic_pressure_bar": "10.0", "fuel_rate_lph": "3.0", "speed_kmh": "0.0",
        "seatbelt": "FASTENED", "cycle_completed": "false", "truck_present": "false",
        "hauler_queue_len": "0", "gps_lat": "37.7749", "gps_lon": "-122.4194", "sig": "deadbeef",
    }
    base.update(overrides)
    return {k.encode(): str(v).encode() for k, v in base.items()}


@pytest.fixture
async def redis_client():
    client = Redis.from_url(settings.redis_url)
    yield client
    await client.aclose()


async def _seed_lesson(operator_id: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Lessons FK-reference a real incident row, so seed a minimal one too."""
    now = datetime.now(timezone.utc)
    incident_id = uuid.uuid4()
    event_id = uuid.uuid4()
    lesson_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(DBEvent(
            id=event_id, type="SEATBELT_VIOLATION", severity="CRITICAL", machine_id=MACHINE,
            operator_id=operator_id, site_id="SITE-A", ts=now, source_engine="test@1.0",
            evidence={"state": "WORKING"}, frame_seq=int(now.timestamp()),
        ))
        session.add(IncidentRow(
            id=incident_id, machine_id=MACHINE, site_id="SITE-A", operator_id=operator_id, task_id=None,
            category="SEATBELT_VIOLATION", severity="CRITICAL", escalated=False, status="OPEN",
            opened_at=now, last_event_at=now, trigger_event_id=event_id, event_count=1,
            risk_level_at_open="NORMAL", explanation_status="PENDING",
        ))
        session.add(LessonRow(
            id=lesson_id, incident_id=incident_id, machine_id=MACHINE, operator_id=operator_id,
            title="Test Lesson", short_tip="Do the safe thing.", explanation="Because it is safer.",
            knowledge_refs=["seatbelt-safety#overview"], source="FALLBACK", status="FALLBACK",
            fallback_reason="NO_API_KEY",
        ))
        await session.commit()
    return lesson_id, incident_id, event_id


async def _cleanup(operator_id: str, incident_id: uuid.UUID | None = None, event_id: uuid.UUID | None = None):
    async with AsyncSessionLocal() as session:
        await session.execute(delete(LessonRow).where(LessonRow.operator_id == operator_id))
        if incident_id is not None:
            await session.execute(delete(IncidentRow).where(IncidentRow.id == incident_id))
        if event_id is not None:
            await session.execute(delete(DBEvent).where(DBEvent.id == event_id))
        await session.commit()


@pytest.mark.asyncio
async def test_lesson_delivered_exactly_once_on_idle_hub_transition(redis_client):
    operator_id = f"{OPERATOR}-{uuid.uuid4().hex[:8]}"
    lesson_id, incident_id, event_id = await _seed_lesson(operator_id)
    try:
        hot_state = MachineHotState(machine_id=MACHINE)
        ts = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as session:
            # Drive well past the IDLE_HUB dwell threshold and beyond —
            # delivery must only happen on the transition edge, not repeat.
            for i in range(IDLE_HUB_DWELL_TICKS + 20):
                raw = make_raw_frame(i, ts, operator_id=operator_id)
                await process_frame(raw, f"{i}-0".encode(), hot_state, redis_client, session, "telemetry:test", "hot-worker")

        async with AsyncSessionLocal() as session:
            lesson = await session.get(LessonRow, lesson_id)
        assert lesson.delivered_at is not None

        first_delivered_at = lesson.delivered_at

        # Simulate leaving and re-entering IDLE_HUB later: with no NEW
        # undelivered lesson, nothing should change on a second transition.
        hot_state2 = MachineHotState(machine_id=MACHINE)
        async with AsyncSessionLocal() as session:
            for i in range(IDLE_HUB_DWELL_TICKS + 5):
                raw = make_raw_frame(1000 + i, ts, operator_id=operator_id)
                await process_frame(raw, f"{1000+i}-0".encode(), hot_state2, redis_client, session, "telemetry:test", "hot-worker")

        async with AsyncSessionLocal() as session:
            lesson_again = await session.get(LessonRow, lesson_id)
        assert lesson_again.delivered_at == first_delivered_at
    finally:
        await _cleanup(operator_id, incident_id, event_id)


@pytest.mark.asyncio
async def test_no_undelivered_lesson_no_push_no_error(redis_client):
    operator_id = f"{OPERATOR}-none-{uuid.uuid4().hex[:8]}"
    hot_state = MachineHotState(machine_id=MACHINE)
    ts = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        for i in range(IDLE_HUB_DWELL_TICKS + 5):
            raw = make_raw_frame(i, ts, operator_id=operator_id)
            # Must not raise even though this operator has no lessons at all.
            await process_frame(raw, f"{i}-0".encode(), hot_state, redis_client, session, "telemetry:test", "hot-worker")

    assert hot_state.last_ui_mode == "IDLE_HUB"
