"""
Full end-to-end pipeline test (Stage 3 Batch 3J):

  Simulator-shaped input -> /ingest/telemetry (real HMAC + freshness +
  sequence checks) -> Hot (safety events) -> Warm (window aggregation) ->
  events stream -> Correlator (incident open/extend) -> incidents:work ->
  Cold (explanation) -> GET /incidents/{id} & /machines/{id}/snapshot.

Runs against real Postgres + Redis (docker-compose up); WarmProcessor,
Correlator.handle_event and ColdWorker.process are driven in-process
(matching how test_warm_worker.py / test_correlator_worker.py /
test_cold_worker.py already exercise them individually) rather than via
live XREADGROUP consumer loops, so the test is deterministic and fast.

Groq is not called — groq_api_key is cleared so ColdWorker.process()
takes its deterministic-fallback path. That path is real production code
(not a mock), and the mocked-success / real-API paths already have
dedicated coverage in backend/tests/unit/test_llm_client.py,
tests/integration/test_cold_worker.py and Batch 3H's manual
scripts/try_llm.py run — this test's job is proving the pipeline wiring,
not re-proving grounding, which 3H already does.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import (
    WindowAggregate, IncidentRow, IncidentEvent, IncidentTimeline, IncidentExplanationRow,
)
from backend.app.main import app
from backend.app.worker.hot import MachineHotState, process_frame, flush_buffers
from backend.app.worker.warm import WarmProcessor
from backend.app.worker.correlator import Correlator
from backend.app.worker.cold import ColdWorker
from backend.app.knowledge.retriever import KnowledgeRetriever
from contracts.demo_assignments import get_assignment
from contracts.events import TelemetryFrame, ProximityReading, Event as EventModel, AlertSeverity
from contracts.signing import sign
from contracts.shard import stream_key, shard_for

pytestmark = pytest.mark.e2e

MACHINE = "EXC001"
SITE = "SITE-A"
SECRET = "secret1"  # matches MACHINE_HMAC_KEYS in .env / seed_db.py for EXC001
HOT_GROUP = "hot-worker"
WARM_GROUP = "warm-worker"

# redis_client / clean_machine fixtures come from tests/e2e/conftest.py


def _frame(seq: int, ts: datetime, **overrides) -> TelemetryFrame:
    assignment = get_assignment(MACHINE)
    base = dict(
        seq=seq, ts=ts, machine_id=MACHINE, operator_id=assignment.operator_id,
        task_id=assignment.task_id, engine_rpm=1950.0, engine_temp_c=92.0,
        hydraulic_pressure_bar=260.0, fuel_rate_lph=14.2, speed_kmh=0.5,
        seatbelt="FASTENED", proximity=None, cycle_completed=False,
        truck_present=True, hauler_queue_len=1, gps=(37.7749, -122.4194), sig="",
    )
    base.update(overrides)
    return TelemetryFrame(**base)


def _signed_payload(frame: TelemetryFrame) -> dict:
    # Mirrors simulator/sim.py's exact round trip: JSON-serialize first
    # (so ts becomes the same string form the wire actually carries), then
    # sign the resulting dict — signing the pydantic object's python-mode
    # dump would canonicalize a real datetime differently than the string
    # ingest.py's verify() ends up hashing.
    payload = json.loads(frame.model_dump_json())
    payload["sig"] = sign(payload, SECRET)
    return payload


async def _init_group(redis: Redis, key: str, group: str) -> None:
    try:
        await redis.xgroup_create(key, group, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


@pytest.mark.asyncio
async def test_full_pipeline_seatbelt_to_explanation(redis_client, clean_machine, monkeypatch):
    monkeypatch.setattr(settings, "warm_window_seconds", 5)
    monkeypatch.setattr(settings, "warm_min_frames", 5)
    monkeypatch.setattr(settings, "groq_api_key", "")  # deterministic fallback (see module docstring)

    tkey = stream_key(MACHINE)
    ekey = f"events:{shard_for(MACHINE)}"
    await _init_group(redis_client, tkey, HOT_GROUP)
    await _init_group(redis_client, tkey, WARM_GROUP)
    # /ingest/telemetry's Lua sequence guard is keyed on machine_id alone
    # and persists in Redis across test runs / manual demo sessions — reset
    # it so this test's seq=1.. is always accepted regardless of history.
    await redis_client.delete(f"telemetry:seq:{MACHINE}")
    # telemetry:{shard} and events:{shard} are the REAL shared streams (also
    # used by manual demo runs) — remember where they were so this test only
    # reads what it itself just published, not stale history.
    last_tid = await redis_client.xrevrange(tkey, count=1)
    tid_floor = last_tid[0][0].decode() if last_tid else "0"
    last_eid = await redis_client.xrevrange(ekey, count=1)
    eid_floor = last_eid[0][0].decode() if last_eid else "0"
    last_wid = await redis_client.xrevrange("incidents:work", count=1)
    wid_floor = last_wid[0][0].decode() if last_wid else "0"

    now = datetime.now(timezone.utc).replace(microsecond=0)
    epoch = int(now.timestamp())
    window_start_epoch = (epoch // settings.warm_window_seconds) * settings.warm_window_seconds
    base_ts = datetime.fromtimestamp(window_start_epoch, tz=timezone.utc)

    # Frame 0: fires both a CRITICAL SEATBELT_VIOLATION and a WARNING
    # PROXIMITY_BREACH on the hot path in one tick (state=WORKING from
    # hyd_bar=260 per core/copilot_core/state.py's cond_working).
    frames = [
        _frame(1, base_ts, seatbelt="UNFASTENED",
               proximity=ProximityReading(zone="ORANGE", distance_m=5.5, source="camera_rear")),
    ]
    # Frames 1..6: plain WORKING frames to fill the (shrunk) 5s warm window
    # past warm_min_frames, seatbelt back to FASTENED so no more hot alerts.
    for i in range(1, 7):
        frames.append(_frame(1 + i, base_ts + timedelta(milliseconds=i * 500)))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for f in frames:
            resp = await client.post("/ingest/telemetry", json=_signed_payload(f))
            assert resp.status_code == 202, resp.text

        # --- Hot path: read back what /ingest/telemetry actually published,
        # exactly what the real hot-worker consumer group would see. ---
        raw_entries = await redis_client.xrange(tkey, min=f"({tid_floor}")
        assert len(raw_entries) == len(frames)

        hot_state = MachineHotState(machine_id=MACHINE)
        async with AsyncSessionLocal() as session:
            for msg_id, raw in raw_entries:
                await process_frame(raw, msg_id, hot_state, redis_client, session, tkey, HOT_GROUP)
            hot_events = [e for _, e in hot_state.event_buffer]
            await flush_buffers(session, MACHINE, hot_state, redis_client, tkey, HOT_GROUP)

        assert {e.type for e in hot_events} == {"SEATBELT_VIOLATION", "PROXIMITY_BREACH"}
        assert any(e.severity == AlertSeverity.CRITICAL for e in hot_events)

        # --- Warm path: same telemetry entries, independent consumer
        # group + processor, window aggregation + ML inference. ---
        warm = WarmProcessor(redis_client)
        for msg_id, raw in raw_entries:
            await warm.handle_message(tkey, msg_id, raw)
        assert MACHINE in warm.open_windows
        open_win = warm.open_windows.pop(MACHINE)
        await warm.close_window(open_win)

        async with AsyncSessionLocal() as session:
            window_row = (await session.execute(
                select(WindowAggregate).where(WindowAggregate.window_id == open_win.window_id)
            )).scalar_one_or_none()
        assert window_row is not None, "warm window was not persisted"
        assert window_row.frame_count == len(frames)

        # Any warm-sourced events (anomaly / idle deviation / eta slip) this
        # window produced — correlated into the SAME incident below if present.
        # Detection is a live ML/statistical decision (real ml/artifacts
        # models against ad-hoc synthetic frames), so this is an
        # opportunistic assertion, not a hard requirement of the chain.
        warm_events_raw = await redis_client.xrange(ekey, min=f"({eid_floor}")
        warm_events = [EventModel.model_validate_json(fields[b"data"]) for _, fields in warm_events_raw]

        # --- Correlator: hot events first (CRITICAL opens the incident,
        # WARNING extends it), then whatever warm emitted. Mirrors
        # correlator.py's own _process_message: handle_event() only decides
        # and persists — publish() (the incidents:work XADD) is a separate
        # step the consumer loop calls afterwards. ---
        correlator = Correlator(redis_client)

        async def _handle_and_publish(e: EventModel) -> str | None:
            action = await correlator.handle_event(e)
            if action:
                async with AsyncSessionLocal() as s:
                    iid = (await s.execute(
                        select(IncidentEvent.incident_id).where(IncidentEvent.event_id == uuid.UUID(e.event_id))
                    )).scalar_one_or_none()
                if iid:
                    await correlator.publish(e, iid, action)
            return action

        actions = []
        for e in sorted(hot_events, key=lambda e: 0 if e.severity == AlertSeverity.CRITICAL else 1):
            actions.append(await _handle_and_publish(e))
        assert actions[0] == "opened"

        for e in warm_events:
            await _handle_and_publish(e)

    async with AsyncSessionLocal() as session:
        incident = (await session.execute(
            select(IncidentRow).where(IncidentRow.machine_id == MACHINE)
        )).scalar_one()
        timeline = (await session.execute(
            select(IncidentTimeline).where(
                IncidentTimeline.incident_id == incident.id, IncidentTimeline.kind == "EVENT",
            )
        )).scalars().all()

    assert incident.status == "OPEN"
    assert incident.severity == "CRITICAL"
    timeline_types = {t.event_type for t in timeline}
    assert {"SEATBELT_VIOLATION", "PROXIMITY_BREACH"} <= timeline_types
    if warm_events:
        assert timeline_types & {e.type for e in warm_events}, \
            "warm-sourced event fired but was not folded into the incident's timeline"

    # --- Cold path: whatever the correlator queued gets explained. ---
    # incident_id is deterministic (contracts.ids.incident_id) — a re-run
    # against the same window can reproduce the same id, so this stream
    # (like telemetry/events above) is read from THIS run's floor only.
    work_entries = await redis_client.xrange("incidents:work", min=f"({wid_floor}")
    queued_ids = {fields[b"incident_id"].decode() for _, fields in work_entries}
    assert str(incident.id) in queued_ids

    retriever = KnowledgeRetriever.from_directory(settings.knowledge_dir)
    cold = ColdWorker(redis_client, retriever)
    outcome = await cold.process(str(incident.id), "opened")
    assert outcome == "FALLBACK"  # groq_api_key cleared above

    async with AsyncSessionLocal() as session:
        explanation = (await session.execute(
            select(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == incident.id)
        )).scalar_one()
    assert explanation.source == "FALLBACK"
    assert explanation.fallback_reason == "NO_API_KEY"

    # --- Frontend read path: the two endpoints the UI actually calls. ---
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/auth/login", json={"username": "supervisor", "password": "demo123"})
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        inc_resp = await client.get(f"/incidents/{incident.id}", headers=headers)
        assert inc_resp.status_code == 200
        inc_body = inc_resp.json()
        assert inc_body["status"] == "OPEN"
        assert inc_body["explanation"] is not None
        assert inc_body["explanation"]["source"] == "FALLBACK"

        snap_resp = await client.get(f"/machines/{MACHINE}/snapshot", headers=headers)
        assert snap_resp.status_code == 200
        snap_body = snap_resp.json()
        assert snap_body["machine_id"] == MACHINE
        open_incident_ids = {i["id"] for i in snap_body["open_incidents"]}
        assert str(incident.id) in open_incident_ids
