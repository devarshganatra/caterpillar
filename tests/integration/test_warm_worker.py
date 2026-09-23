"""
Integration tests for the warm worker's full close_window pipeline —
against real Postgres + Redis (docker-compose up), real ml/artifacts.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, select

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import WindowAggregate, EtaEstimateRow, Event as DBEvent
from backend.app.worker.warm import WarmProcessor
from backend.app.services.ml_registry import reset_registry_for_tests
from ml.features import compute_window_features
import numpy as np

pytestmark = pytest.mark.integration


def make_raw_frame(seq: int, machine_id: str, ts: datetime, task_id="TASK-001",
                    operator_id="11111111-1111-1111-1111-111111111111", **overrides) -> dict:
    base = {
        "seq": str(seq), "ts": ts.isoformat(), "machine_id": machine_id,
        "operator_id": operator_id, "task_id": task_id,
        "engine_rpm": "1950.0", "engine_temp_c": "92.0", "hydraulic_pressure_bar": "260.0",
        "fuel_rate_lph": "14.2", "speed_kmh": "0.5", "seatbelt": "FASTENED",
        "cycle_completed": "false", "truck_present": "true", "hauler_queue_len": "1",
        "gps_lat": "37.7749", "gps_lon": "-122.4194", "sig": "deadbeef",
    }
    base.update(overrides)
    return {k.encode(): str(v).encode() for k, v in base.items()}


@pytest.fixture
async def redis_client():
    client = Redis.from_url(settings.redis_url)
    yield client
    await client.aclose()


def _aligned_ts(offset_hours: float) -> datetime:
    """
    Window boundaries are absolute-epoch-aligned (floor(ts/window_s)*window_s),
    NOT relative to whenever a test happens to start. datetime.now() + offset
    lands on an arbitrary second, so a naive base_ts would non-deterministically
    straddle a window boundary partway through a sequence of "consecutive"
    frames. Align explicitly to the next warm_window_seconds boundary.
    """
    ts = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=offset_hours)
    epoch = int(ts.timestamp())
    aligned_epoch = ((epoch // settings.warm_window_seconds) + 1) * settings.warm_window_seconds
    return datetime.fromtimestamp(aligned_epoch, tz=timezone.utc)


async def _cleanup(machine_id: str, task_id: str | None, window_start_after: datetime):
    async with AsyncSessionLocal() as session:
        await session.execute(delete(WindowAggregate).where(
            WindowAggregate.machine_id == machine_id, WindowAggregate.window_start >= window_start_after,
        ))
        if task_id:
            await session.execute(delete(EtaEstimateRow).where(EtaEstimateRow.task_id == task_id))
        await session.execute(delete(DBEvent).where(
            DBEvent.machine_id == machine_id, DBEvent.ts >= window_start_after,
            DBEvent.source_engine.in_(["anomaly@iforest", "attribution@1.0"]) | DBEvent.source_engine.like("anomaly@%") | DBEvent.source_engine.like("eta@%"),
        ))
        await session.commit()


async def _feed_window(processor: WarmProcessor, machine_id: str, base_ts: datetime, n_frames: int,
                        start_seq: int, task_id="TASK-001", cycle_every: int = 50, **overrides) -> None:
    """
    Feeds n_frames 1-second-apart frames, then closes that machine's open
    window DIRECTLY (rather than via a synthetic "future" frame through
    handle_message) — using a boundary-forcing frame instead would leave an
    orphan 1-frame window behind that only gets closed (and persisted as its
    own extra minimal row) whenever a LATER call's frames happen to reach
    its lateness grace period, which corrupts row-count assertions in
    multi-window tests.
    """
    for i in range(n_frames):
        ts = base_ts + timedelta(seconds=i)
        cycle_completed = "true" if (i > 0 and i % cycle_every == 0) else "false"
        raw = make_raw_frame(start_seq + i, machine_id, ts, task_id=task_id,
                              cycle_completed=cycle_completed, **overrides)
        await processor.handle_message("telemetry:test", f"{start_seq + i}-0".encode(), raw)
    win = processor.open_windows.pop(machine_id, None)
    if win is not None:
        await processor.close_window(win)


@pytest.mark.asyncio
async def test_aggregation_matches_compute_window_features(redis_client):
    machine_id = "EXC001"
    base_ts = _aligned_ts(1)
    await _cleanup(machine_id, "TASK-001", base_ts - timedelta(seconds=5))

    processor = WarmProcessor(redis_client)
    # n_frames must fit within warm_window_seconds (30) or the frames span
    # two windows (each frame here is 1s apart).
    await _feed_window(processor, machine_id, base_ts, n_frames=25, start_seq=900001, cycle_every=12)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(WindowAggregate).where(
            WindowAggregate.machine_id == machine_id, WindowAggregate.window_start >= base_ts - timedelta(seconds=5),
        ))
        rows = result.scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.frame_count == 25
    assert row.rpm_mean == pytest.approx(1950.0, abs=0.01)
    assert row.working_ratio == pytest.approx(1.0)  # hyd=260 >= 120 for all frames
    assert row.cycle_count == 2  # cycle_completed at i=12, i=24 out of 25 frames

    await _cleanup(machine_id, "TASK-001", base_ts - timedelta(seconds=5))


@pytest.mark.asyncio
async def test_reprocessing_same_window_creates_no_duplicate_row(redis_client):
    machine_id = "EXC001"
    base_ts = _aligned_ts(2)
    await _cleanup(machine_id, "TASK-001", base_ts - timedelta(seconds=5))

    processor1 = WarmProcessor(redis_client)
    await _feed_window(processor1, machine_id, base_ts, n_frames=25, start_seq=910001)

    # Simulate redelivery after a "restart": a fresh processor with no
    # in-memory state reprocesses the exact same frames.
    processor2 = WarmProcessor(redis_client)
    await _feed_window(processor2, machine_id, base_ts, n_frames=25, start_seq=910001)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(WindowAggregate).where(
            WindowAggregate.machine_id == machine_id, WindowAggregate.window_start >= base_ts - timedelta(seconds=5),
        ))
        rows = result.scalars().all()

    assert len(rows) == 1, f"expected exactly 1 window row, got {len(rows)}"
    await _cleanup(machine_id, "TASK-001", base_ts - timedelta(seconds=5))


@pytest.mark.asyncio
async def test_restart_recover_prevents_reprocessing(redis_client):
    machine_id = "EXC001"
    base_ts = _aligned_ts(3)
    await _cleanup(machine_id, "TASK-001", base_ts - timedelta(seconds=5))

    processor1 = WarmProcessor(redis_client)
    await _feed_window(processor1, machine_id, base_ts, n_frames=22, start_seq=920001)

    # New processor instance (simulating a restart) calls recover() to load
    # persisted_last_seq from the DB, then reprocesses the SAME frames.
    processor2 = WarmProcessor(redis_client)
    await processor2.recover()
    assert processor2.persisted_last_seq.get(machine_id, -1) >= 920001 + 21

    await _feed_window(processor2, machine_id, base_ts, n_frames=22, start_seq=920001)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(WindowAggregate).where(
            WindowAggregate.machine_id == machine_id, WindowAggregate.window_start >= base_ts - timedelta(seconds=5),
        ))
        rows = result.scalars().all()
    assert len(rows) == 1

    await _cleanup(machine_id, "TASK-001", base_ts - timedelta(seconds=5))


@pytest.mark.asyncio
async def test_insufficient_frames_persists_minimal_row_no_crash(redis_client):
    machine_id = "EXC001"
    base_ts = _aligned_ts(4)
    await _cleanup(machine_id, "TASK-001", base_ts - timedelta(seconds=5))

    processor = WarmProcessor(redis_client)
    # fewer than warm_min_frames (20)
    await _feed_window(processor, machine_id, base_ts, n_frames=5, start_seq=930001)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(WindowAggregate).where(
            WindowAggregate.machine_id == machine_id, WindowAggregate.window_start >= base_ts - timedelta(seconds=5),
        ))
        rows = result.scalars().all()

    assert len(rows) == 1
    assert rows[0].frame_count == 5
    assert rows[0].rpm_mean is None
    assert rows[0].anomaly["reason"] == "INSUFFICIENT_FRAMES"

    await _cleanup(machine_id, "TASK-001", base_ts - timedelta(seconds=5))


@pytest.mark.asyncio
async def test_eta_progress_accumulates_across_windows(redis_client):
    machine_id = "EXC001"
    base_ts = _aligned_ts(5)
    await _cleanup(machine_id, "TASK-001", base_ts - timedelta(seconds=5))

    processor = WarmProcessor(redis_client)
    # Window 1: 2 cycles (n_frames must fit within warm_window_seconds=30)
    await _feed_window(processor, machine_id, base_ts, n_frames=25, start_seq=940001, cycle_every=12)
    # Window 2: starts right after window 1's boundary frame
    # One window later, grid-aligned (base_ts already is, and window_seconds divides evenly).
    base_ts2 = base_ts + timedelta(seconds=settings.warm_window_seconds)
    await _feed_window(processor, machine_id, base_ts2, n_frames=25, start_seq=941001, cycle_every=12)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(WindowAggregate).where(
            WindowAggregate.machine_id == machine_id, WindowAggregate.window_start >= base_ts - timedelta(seconds=5),
        ).order_by(WindowAggregate.window_start))
        rows = result.scalars().all()

        eta_result = await session.execute(select(EtaEstimateRow).where(
            EtaEstimateRow.task_id == "TASK-001", EtaEstimateRow.kind == "LIVE",
        ).order_by(EtaEstimateRow.ts))
        eta_rows = eta_result.scalars().all()

    assert len(rows) == 2
    assert len(eta_rows) == 2
    # cycles_done in the 2nd LIVE row must reflect window1's cycles PLUS window2's
    assert eta_rows[1].payload["cycles_done"] > eta_rows[0].payload["cycles_done"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(EtaEstimateRow).where(
            EtaEstimateRow.task_id == "TASK-001", EtaEstimateRow.kind == "BASELINE",
        ))
        baseline_rows = result.scalars().all()
    assert len(baseline_rows) == 1, "exactly one BASELINE row must exist even after 2 windows"

    await _cleanup(machine_id, "TASK-001", base_ts - timedelta(seconds=5))


@pytest.mark.asyncio
async def test_model_unavailable_degrades_gracefully(redis_client, monkeypatch):
    """With an empty artifacts dir, anomaly/ETA must report UNAVAILABLE, not crash."""
    from backend.app.services import ml_registry as ml_registry_module

    machine_id = "LDR002"  # not assigned a task in seed_db.py -> ETA path also exercises NO_TASK
    base_ts = _aligned_ts(6)
    await _cleanup(machine_id, None, base_ts - timedelta(seconds=5))

    reset_registry_for_tests()
    monkeypatch.setattr(settings, "ml_artifacts_dir", "/tmp/nonexistent_artifacts_dir_for_test")

    try:
        processor = WarmProcessor(redis_client)
        await _feed_window(processor, machine_id, base_ts, n_frames=25, start_seq=950001,
                            task_id="", operator_id="UNASSIGNED", cycle_every=12)

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(WindowAggregate).where(
                WindowAggregate.machine_id == machine_id, WindowAggregate.window_start >= base_ts - timedelta(seconds=5),
            ))
            rows = result.scalars().all()

        assert len(rows) == 1
        assert rows[0].anomaly["method"] in ("UNAVAILABLE", "ROBUST_Z")
    finally:
        reset_registry_for_tests()  # restore real artifacts for subsequent tests
        await _cleanup(machine_id, None, base_ts - timedelta(seconds=5))
