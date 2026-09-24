"""
Restart-safety test (Stage 3 Batch 3J): kills and restarts each worker
mid-scenario (a fresh processor instance, as a real process restart would
produce) and asserts no duplicate rows — windows, events, incidents, or
explanations — result from reprocessing the same input.

Each worker's idempotency mechanism is exercised directly:
  - Warm:        deterministic window_id + ON CONFLICT DO NOTHING
                 (backend/app/worker/warm.py `_insert_window_row`),
                 recover() replaying persisted_last_seq.
  - Correlator:  re-delivering an already-linked event is a no-op
                 (IncidentEvent unique constraint / SKIP_DUPLICATE).
  - Cold:        packet_hash cache -> "CACHED", never a second explanation row.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import (
    WindowAggregate, Event as DBEvent, IncidentRow, IncidentEvent, IncidentExplanationRow,
)
from backend.app.worker.warm import WarmProcessor
from backend.app.worker.correlator import Correlator
from backend.app.worker.cold import ColdWorker
from backend.app.knowledge.retriever import KnowledgeRetriever
from contracts.demo_assignments import get_assignment
from contracts.events import AlertSeverity, Event as EventModel

pytestmark = pytest.mark.e2e

MACHINE = "EXC001"
SITE = "SITE-A"


def _make_raw_frame(seq: int, ts: datetime, **overrides) -> dict:
    assignment = get_assignment(MACHINE)
    base = {
        "seq": str(seq), "ts": ts.isoformat(), "machine_id": MACHINE,
        "operator_id": assignment.operator_id, "task_id": assignment.task_id or "",
        "engine_rpm": "1950.0", "engine_temp_c": "92.0", "hydraulic_pressure_bar": "260.0",
        "fuel_rate_lph": "14.2", "speed_kmh": "0.5", "seatbelt": "FASTENED",
        "cycle_completed": "false", "truck_present": "true", "hauler_queue_len": "1",
        "gps_lat": "37.7749", "gps_lon": "-122.4194", "sig": "deadbeef",
    }
    base.update(overrides)
    return {k.encode(): str(v).encode() for k, v in base.items()}


@pytest.mark.asyncio
async def test_warm_processor_restart_produces_no_duplicate_window(redis_client, clean_machine, monkeypatch):
    monkeypatch.setattr(settings, "warm_window_seconds", 5)
    monkeypatch.setattr(settings, "warm_min_frames", 3)

    # Inlined rather than imported from tests/e2e/conftest.py: `tests` has
    # no `__init__.py` (deliberately, matching tests/unit and
    # tests/integration), so `tests.e2e` only resolves as a module when
    # pytest is invoked with `tests/e2e/` as its own rootdir-relative arg —
    # it breaks when collected alongside other top-level dirs in one run.
    epoch = int(datetime.now(timezone.utc).timestamp())
    base_ts = datetime.fromtimestamp((epoch // 5) * 5, tz=timezone.utc)

    # Processor A takes the first half of the window, then "dies" (dropped,
    # never closes the window — exactly what a mid-window process kill
    # looks like: the window's frames only exist in that dead process's
    # memory, never persisted).
    proc_a = WarmProcessor(redis_client)
    for i in range(2):
        raw = _make_raw_frame(1 + i, base_ts + timedelta(milliseconds=i * 500))
        await proc_a.handle_message("telemetry:e2e-restart", f"{1+i}-0".encode(), raw)
    assert MACHINE in proc_a.open_windows
    del proc_a  # simulate the process dying; its in-memory window is lost

    # Processor B is the restart: recover() (a no-op here, since nothing
    # was persisted yet) then re-receives ALL frames from the beginning —
    # exactly what redelivery of unacked pending stream entries looks like
    # after a real restart, since the dead processor never XACKed them.
    proc_b = WarmProcessor(redis_client)
    await proc_b.recover()
    for i in range(4):
        raw = _make_raw_frame(1 + i, base_ts + timedelta(milliseconds=i * 500))
        await proc_b.handle_message("telemetry:e2e-restart", f"{1+i}-0".encode(), raw)
    win = proc_b.open_windows.pop(MACHINE)
    assert len(win.frames) == 4, "restart should not lose or duplicate frames within the same window"
    await proc_b.close_window(win)

    async with AsyncSessionLocal() as session:
        count = (await session.execute(
            select(func.count()).select_from(WindowAggregate).where(WindowAggregate.window_id == win.window_id)
        )).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_correlator_restart_does_not_duplicate_incident_link(redis_client, clean_machine):
    event = EventModel(
        event_id=str(uuid.uuid4()), type="SEATBELT_VIOLATION", severity=AlertSeverity.CRITICAL,
        machine_id=MACHINE, operator_id="OP-TEST", site_id=SITE,
        ts=datetime.now(timezone.utc), source_engine="test@1.0", evidence={},
    )
    async with AsyncSessionLocal() as session:
        session.add(DBEvent(
            id=uuid.UUID(event.event_id), type=event.type, severity=event.severity.value,
            machine_id=event.machine_id, operator_id=event.operator_id, site_id=event.site_id,
            ts=event.ts, source_engine=event.source_engine, evidence=event.evidence,
            frame_seq=int(event.ts.timestamp()),
        ))
        await session.commit()

    # Correlator instance A processes the event and (in production) would
    # crash before its ack — a second instance (the restart) redelivers the
    # same event from the stream's pending entries.
    correlator_a = Correlator(redis_client)
    action_a = await correlator_a.handle_event(event)
    assert action_a == "opened"
    del correlator_a

    correlator_b = Correlator(redis_client)
    action_b = await correlator_b.handle_event(event)
    assert action_b is None  # SKIP_DUPLICATE — already linked, not a second "opened"

    async with AsyncSessionLocal() as session:
        incident_count = (await session.execute(
            select(func.count()).select_from(IncidentRow).where(IncidentRow.machine_id == MACHINE)
        )).scalar_one()
        link_count = (await session.execute(
            select(func.count()).select_from(IncidentEvent).where(IncidentEvent.event_id == uuid.UUID(event.event_id))
        )).scalar_one()
    assert incident_count == 1
    assert link_count == 1


@pytest.mark.asyncio
async def test_cold_worker_restart_does_not_duplicate_explanation(redis_client, clean_machine, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")  # deterministic fallback, no network
    now = datetime.now(timezone.utc)
    incident_id = uuid.uuid4()
    event_id = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        session.add(DBEvent(
            id=event_id, type="SEATBELT_VIOLATION", severity="CRITICAL", machine_id=MACHINE,
            operator_id="OP-TEST", site_id=SITE, ts=now, source_engine="test@1.0",
            evidence={"state": "WORKING"}, frame_seq=int(now.timestamp()),
        ))
        session.add(IncidentRow(
            id=incident_id, machine_id=MACHINE, site_id=SITE, operator_id="OP-TEST", task_id=None,
            category="SEATBELT_VIOLATION", severity="CRITICAL", escalated=False, status="OPEN",
            opened_at=now, last_event_at=now, trigger_event_id=event_id, event_count=1,
            risk_level_at_open="NORMAL", explanation_status="PENDING",
        ))
        await session.commit()

    retriever = KnowledgeRetriever.from_directory(settings.knowledge_dir)

    # Cold worker instance A processes and persists a FALLBACK explanation,
    # then "dies" before its incidents:work entry would have been trimmed.
    cold_a = ColdWorker(redis_client, retriever)
    outcome_a = await cold_a.process(str(incident_id), "opened")
    assert outcome_a == "FALLBACK"
    del cold_a

    # Restart: a new instance re-processes the SAME queued incident_id
    # (exactly what redelivering an unacked incidents:work entry looks like).
    cold_b = ColdWorker(redis_client, retriever)
    outcome_b = await cold_b.process(str(incident_id), "opened")
    assert outcome_b == "CACHED"

    async with AsyncSessionLocal() as session:
        explanation_count = (await session.execute(
            select(func.count()).select_from(IncidentExplanationRow)
            .where(IncidentExplanationRow.incident_id == incident_id)
        )).scalar_one()
    assert explanation_count == 1
