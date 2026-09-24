"""
Integration tests for the correlator's full handle_event pipeline — real
Postgres + Redis. Fewer, higher-signal tests than the warm worker suite
(the pure decision logic already has thorough unit coverage in
backend/tests/unit/test_correlator.py); these exercise the DB
read/write/lock path that unit tests can't.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, select

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import Event as DBEvent, IncidentRow, IncidentEvent, IncidentTimeline, IncidentExplanationRow, LessonRow
from backend.app.worker.correlator import Correlator, _reconcile
from contracts.events import Event as EventModel, AlertSeverity

pytestmark = pytest.mark.integration

MACHINE = "EXC001"
SITE = "SITE-A"


def make_event(event_type="SEATBELT_VIOLATION", severity=AlertSeverity.WARNING,
                ts=None, evidence=None, machine_id=MACHINE) -> EventModel:
    return EventModel(
        event_id=str(uuid.uuid4()), type=event_type, severity=severity,
        machine_id=machine_id, operator_id="OP-TEST", site_id=SITE,
        ts=ts or datetime.now(timezone.utc), source_engine="test@1.0", evidence=evidence or {},
    )


_frame_seq_counter = 0


async def _persist(event: EventModel) -> None:
    """Correlator reads events back from the DB (for prior-event pull-in and
    reconcile), so tests must actually persist them, not just hold them in
    memory. Each event needs its own frame_seq: the uq_events_machine_seq_type
    constraint is (machine_id, frame_seq, type), and several tests persist
    multiple events of the same type for the same machine."""
    global _frame_seq_counter
    _frame_seq_counter += 1
    async with AsyncSessionLocal() as session:
        session.add(DBEvent(
            id=uuid.UUID(event.event_id), type=event.type, severity=event.severity.value,
            machine_id=event.machine_id, operator_id=event.operator_id, site_id=event.site_id,
            ts=event.ts, source_engine=event.source_engine, evidence=event.evidence,
            frame_seq=_frame_seq_counter,
        ))
        await session.commit()


@pytest.fixture
async def redis_client():
    client = Redis.from_url(settings.redis_url)
    yield client
    await client.aclose()


@pytest.fixture
async def clean_machine():
    async def _clean():
        async with AsyncSessionLocal() as session:
            incidents = (await session.execute(select(IncidentRow.id).where(IncidentRow.machine_id == MACHINE))).scalars().all()
            for iid in incidents:
                # incident_explanations (Batch 3H) and lessons (Stage 4A)
                # both FK-reference incidents.id; a stray manual cold-worker
                # run against this same dev DB (e.g.
                # `python -c "... ColdWorker(...).process(...)"`) leaves a
                # real row in either that would otherwise block deleting
                # the incident below with a ForeignKeyViolationError.
                await session.execute(delete(LessonRow).where(LessonRow.incident_id == iid))
                await session.execute(delete(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == iid))
                await session.execute(delete(IncidentTimeline).where(IncidentTimeline.incident_id == iid))
                await session.execute(delete(IncidentEvent).where(IncidentEvent.incident_id == iid))
            await session.execute(delete(IncidentRow).where(IncidentRow.machine_id == MACHINE))
            # Delete ALL events for this machine, not just ones this fixture
            # itself created: a stray manual verification run against the
            # same dev DB (e.g. `make demo` against EXC001) leaves real
            # events with recent timestamps that _open_incident's "pull in
            # prior events within the window" logic would otherwise
            # accidentally fold into a freshly-opened test incident.
            await session.execute(delete(DBEvent).where(DBEvent.machine_id == MACHINE))
            await session.commit()
    await _clean()
    yield
    await _clean()


@pytest.mark.asyncio
async def test_warning_opens_incident(redis_client, clean_machine):
    correlator = Correlator(redis_client)
    event = make_event(severity=AlertSeverity.WARNING)
    await _persist(event)

    action = await correlator.handle_event(event)
    assert action == "opened"

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(IncidentRow).where(IncidentRow.machine_id == MACHINE))
        rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "OPEN"
    assert rows[0].severity == "WARNING"


@pytest.mark.asyncio
async def test_info_alone_opens_nothing(redis_client, clean_machine):
    correlator = Correlator(redis_client)
    event = make_event(severity=AlertSeverity.INFO)
    await _persist(event)

    action = await correlator.handle_event(event)
    assert action is None

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(IncidentRow).where(IncidentRow.machine_id == MACHINE))
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_second_event_within_window_extends(redis_client, clean_machine):
    correlator = Correlator(redis_client)
    t0 = datetime.now(timezone.utc)
    e1 = make_event(event_type="SEATBELT_VIOLATION", severity=AlertSeverity.WARNING, ts=t0)
    await _persist(e1)
    await correlator.handle_event(e1)

    e2 = make_event(event_type="PROXIMITY_BREACH", severity=AlertSeverity.WARNING, ts=t0 + timedelta(minutes=4))
    await _persist(e2)
    action = await correlator.handle_event(e2)
    assert action == "extended"

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(IncidentRow).where(IncidentRow.machine_id == MACHINE))
        rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].event_count == 2


@pytest.mark.asyncio
async def test_event_after_window_opens_new_incident(redis_client, clean_machine):
    correlator = Correlator(redis_client)
    t0 = datetime.now(timezone.utc)
    e1 = make_event(ts=t0)
    await _persist(e1)
    await correlator.handle_event(e1)

    e2 = make_event(ts=t0 + timedelta(minutes=6))  # past correlation_window_s (300s)
    await _persist(e2)
    action = await correlator.handle_event(e2)
    assert action == "opened"

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(IncidentRow).where(IncidentRow.machine_id == MACHINE))
        rows = result.scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_critical_escalates_and_reopens_acknowledged(redis_client, clean_machine):
    correlator = Correlator(redis_client)
    t0 = datetime.now(timezone.utc)
    e1 = make_event(severity=AlertSeverity.WARNING, ts=t0)
    await _persist(e1)
    await correlator.handle_event(e1)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(IncidentRow).where(IncidentRow.machine_id == MACHINE))
        row = result.scalar_one()
        row.status = "ACKNOWLEDGED"
        await session.execute(IncidentRow.__table__.update().where(IncidentRow.id == row.id).values(status="ACKNOWLEDGED"))
        await session.commit()

    e2 = make_event(severity=AlertSeverity.CRITICAL, ts=t0 + timedelta(minutes=1))
    await _persist(e2)
    action = await correlator.handle_event(e2)
    assert action == "escalated"

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(IncidentRow).where(IncidentRow.machine_id == MACHINE))
        row = result.scalar_one()
    assert row.severity == "CRITICAL"
    assert row.escalated is True
    assert row.status == "OPEN"  # re-opened from ACKNOWLEDGED


@pytest.mark.asyncio
async def test_duplicate_event_delivered_three_times_links_once(redis_client, clean_machine):
    correlator = Correlator(redis_client)
    event = make_event()
    await _persist(event)

    a1 = await correlator.handle_event(event)
    a2 = await correlator.handle_event(event)
    a3 = await correlator.handle_event(event)
    assert a1 == "opened"
    assert a2 is None and a3 is None  # SKIP_DUPLICATE

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(IncidentEvent).where(IncidentEvent.event_id == uuid.UUID(event.event_id)))
        links = result.scalars().all()
        incident_result = await session.execute(select(IncidentRow).where(IncidentRow.machine_id == MACHINE))
        incident = incident_result.scalar_one()
    assert len(links) == 1
    assert incident.event_count == 1


@pytest.mark.asyncio
async def test_timeline_reads_in_event_time_order_regardless_of_processing_order(redis_client, clean_machine):
    correlator = Correlator(redis_client)
    t0 = datetime.now(timezone.utc)
    e_t1 = make_event(event_type="SEATBELT_VIOLATION", ts=t0 + timedelta(seconds=1))
    e_t2 = make_event(event_type="PROXIMITY_BREACH", evidence={"zone": "RED"}, ts=t0 + timedelta(seconds=2))
    e_t3 = make_event(event_type="OVERSPEED_CONDITION", ts=t0 + timedelta(seconds=3))
    for e in (e_t1, e_t2, e_t3):
        await _persist(e)

    # Process out of order: t3, t1, t2
    await correlator.handle_event(e_t3)
    await correlator.handle_event(e_t1)
    await correlator.handle_event(e_t2)

    async with AsyncSessionLocal() as session:
        incident = (await session.execute(select(IncidentRow).where(IncidentRow.machine_id == MACHINE))).scalar_one()
        result = await session.execute(
            select(IncidentTimeline).where(IncidentTimeline.incident_id == incident.id, IncidentTimeline.kind == "EVENT")
            .order_by(IncidentTimeline.first_ts, IncidentTimeline.id)
        )
        entries = result.scalars().all()

    assert [e.first_ts for e in entries] == sorted(e.first_ts for e in entries)
    assert entries[0].event_type == "SEATBELT_VIOLATION"
    assert entries[-1].event_type == "OVERSPEED_CONDITION"


@pytest.mark.asyncio
async def test_repeated_same_key_events_merge_into_one_timeline_entry(redis_client, clean_machine):
    correlator = Correlator(redis_client)
    t0 = datetime.now(timezone.utc)
    first = None
    for i in range(15):
        e = make_event(event_type="SEATBELT_VIOLATION", ts=t0 + timedelta(seconds=i * 0.5))
        await _persist(e)
        action = await correlator.handle_event(e)
        if first is None:
            first = action

    assert first == "opened"
    async with AsyncSessionLocal() as session:
        incident = (await session.execute(select(IncidentRow).where(IncidentRow.machine_id == MACHINE))).scalar_one()
        result = await session.execute(
            select(IncidentTimeline).where(IncidentTimeline.incident_id == incident.id, IncidentTimeline.kind == "EVENT")
        )
        entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].count == 15
    assert incident.event_count == 15


@pytest.mark.asyncio
async def test_reconcile_links_an_event_that_only_exists_in_db(redis_client, clean_machine):
    """Simulates the crash window between a producer's DB commit and its XADD:
    an event exists in `events` but was never correlated. reconcile() must catch it."""
    correlator = Correlator(redis_client)
    event = make_event(severity=AlertSeverity.WARNING)
    await _persist(event)  # in DB, but handle_event() never called -> not in incident_events

    await _reconcile(correlator)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(IncidentEvent.event_id).where(IncidentEvent.event_id == uuid.UUID(event.event_id)))
        assert result.scalar_one_or_none() is not None


def test_warm_worker_does_not_import_correlator_or_incident_models():
    """
    The warm worker must never create incidents directly — only the
    correlator does. Parses warm.py's AST for actual import statements
    (not a substring check on the source: the module docstring legitimately
    mentions "correlator" as a concept, e.g. "events for the correlator").
    """
    import ast
    import inspect
    import backend.app.worker.warm as warm_module

    tree = ast.parse(inspect.getsource(warm_module))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_names.add(node.module or "")
            imported_names.update(alias.name for alias in node.names)

    assert not any("correlator" in name for name in imported_names), imported_names
    assert "IncidentRow" not in imported_names
    assert "IncidentEvent" not in imported_names
    assert "IncidentTimeline" not in imported_names
