"""
Integration tests for Batch 3.0 fixes G1/G2/G3/G4: hot worker events stream,
site scoping, per-frame frame_seq, and deterministic event IDs — against a
real Postgres + Redis (docker-compose up).
"""
import time
from datetime import datetime, timezone

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, select

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import Event as DBEvent
from backend.app.worker.hot import MachineHotState, process_frame, flush_buffers
from contracts.shard import shard_for

pytestmark = pytest.mark.integration


def make_raw_frame(seq: int, machine_id: str, ts: datetime, **overrides) -> dict:
    base = {
        "seq": str(seq),
        "ts": ts.isoformat(),
        "machine_id": machine_id,
        "operator_id": "OP1",
        "task_id": "TASK-001",
        "engine_rpm": "1950.0",
        "engine_temp_c": "92.0",
        "hydraulic_pressure_bar": "280.0",
        "fuel_rate_lph": "14.2",
        "speed_kmh": "3.5",
        "seatbelt": "FASTENED",
        "cycle_completed": "false",
        "truck_present": "true",
        "hauler_queue_len": "1",
        "gps_lat": "37.7749",
        "gps_lon": "-122.4194",
        "sig": "deadbeef",
    }
    base.update(overrides)
    return {k.encode(): v.encode() for k, v in base.items()}


@pytest.fixture
async def redis_client():
    client = Redis.from_url(settings.redis_url)
    yield client
    await client.aclose()


async def _clean_events(machine_id: str, seqs: list[int]):
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(DBEvent).where(DBEvent.machine_id == machine_id, DBEvent.frame_seq.in_(seqs))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_events_persist_and_stream_for_two_frames(redis_client):
    """
    G1 + G3: two consecutive frames that each raise a WARNING event must
    result in TWO DB rows (distinct frame_seq) and TWO messages on the
    events:{shard} stream — not one collapsed row/message.
    """
    machine_id = "EXC001"
    base_seq = int(time.time())  # unique-ish per test run
    seqs = [base_seq, base_seq + 1]
    await _clean_events(machine_id, seqs)

    shard = shard_for(machine_id)
    stream_key = f"events:{shard}"
    xlen_before = await redis_client.xlen(stream_key)

    hot_state = MachineHotState(machine_id=machine_id)
    ts = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        for i, seq in enumerate(seqs):
            raw = make_raw_frame(seq, machine_id, ts, seatbelt="UNFASTENED", speed_kmh="3.0")
            await process_frame(raw, f"{seq}-0".encode(), hot_state, redis_client, session,
                                 "telemetry:0", "hot-worker")
        # Force a flush regardless of the 1s wall-clock throttle
        await flush_buffers(session, machine_id, hot_state, redis_client, "telemetry:0", "hot-worker")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DBEvent).where(DBEvent.machine_id == machine_id, DBEvent.frame_seq.in_(seqs))
        )
        rows = result.scalars().all()

    assert len(rows) == 2, f"expected 2 distinct DB rows, got {len(rows)}: {[r.frame_seq for r in rows]}"
    assert sorted(r.frame_seq for r in rows) == seqs

    xlen_after = await redis_client.xlen(stream_key)
    assert xlen_after - xlen_before == 2, "expected exactly 2 new messages on the events stream"

    await _clean_events(machine_id, seqs)


@pytest.mark.asyncio
async def test_replaying_same_message_creates_no_duplicate_row(redis_client):
    """Reprocessing the identical frame (simulating XREADGROUP redelivery) must not create a second row."""
    machine_id = "EXC001"
    seq = int(time.time()) + 1000
    await _clean_events(machine_id, [seq])

    ts = datetime.now(timezone.utc)
    raw = make_raw_frame(seq, machine_id, ts, seatbelt="UNFASTENED", speed_kmh="3.0")

    async with AsyncSessionLocal() as session:
        hot_state = MachineHotState(machine_id=machine_id)
        await process_frame(raw, f"{seq}-0".encode(), hot_state, redis_client, session, "telemetry:0", "hot-worker")
        await flush_buffers(session, machine_id, hot_state, redis_client, "telemetry:0", "hot-worker")

        # Simulate redelivery: fresh hot_state (as if worker restarted with no
        # in-memory last_seq) reprocesses the exact same frame.
        hot_state2 = MachineHotState(machine_id=machine_id)
        await process_frame(raw, f"{seq}-0".encode(), hot_state2, redis_client, session, "telemetry:0", "hot-worker")
        await flush_buffers(session, machine_id, hot_state2, redis_client, "telemetry:0", "hot-worker")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DBEvent).where(DBEvent.machine_id == machine_id, DBEvent.frame_seq == seq)
        )
        rows = result.scalars().all()

    assert len(rows) == 1, "replaying the same frame must not create a duplicate DB row"
    await _clean_events(machine_id, [seq])


@pytest.mark.asyncio
async def test_site_b_machine_events_carry_site_b(redis_client):
    """G2: EXC003/EXC004 are SITE-B per contracts.machine_config; events must reflect that, not a hardcoded SITE-A."""
    machine_id = "EXC003"
    seq = int(time.time()) + 2000
    await _clean_events(machine_id, [seq])

    ts = datetime.now(timezone.utc)
    raw = make_raw_frame(seq, machine_id, ts, seatbelt="UNFASTENED", speed_kmh="2.0")

    async with AsyncSessionLocal() as session:
        hot_state = MachineHotState(machine_id=machine_id)
        await process_frame(raw, f"{seq}-0".encode(), hot_state, redis_client, session, "telemetry:0", "hot-worker")
        await flush_buffers(session, machine_id, hot_state, redis_client, "telemetry:0", "hot-worker")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DBEvent).where(DBEvent.machine_id == machine_id, DBEvent.frame_seq == seq)
        )
        rows = result.scalars().all()

    assert rows, "expected a SEATBELT_VIOLATION event"
    assert all(r.site_id == "SITE-B" for r in rows)
    await _clean_events(machine_id, [seq])
