"""
Unit tests for backend.app.worker.warm's window-boundary logic
(handle_message / OpenWindow), using a mocked Redis and a stubbed
close_window so these run without a DB. The full close_window pipeline is
covered by tests/integration/test_warm_worker.py.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.app.worker.warm import WarmProcessor, _window_start_epoch
from backend.app.config import settings

pytestmark = pytest.mark.unit


def make_raw_frame(seq: int, machine_id: str, ts: datetime, **overrides) -> dict:
    base = {
        "seq": str(seq), "ts": ts.isoformat(), "machine_id": machine_id,
        "operator_id": "OP1", "task_id": "TASK-001",
        "engine_rpm": "1950.0", "engine_temp_c": "92.0", "hydraulic_pressure_bar": "280.0",
        "fuel_rate_lph": "14.2", "speed_kmh": "3.5", "seatbelt": "FASTENED",
        "cycle_completed": "false", "truck_present": "true", "hauler_queue_len": "1",
        "gps_lat": "37.7749", "gps_lon": "-122.4194", "sig": "deadbeef",
    }
    base.update(overrides)
    return {k.encode(): str(v).encode() for k, v in base.items()}


@pytest.fixture
def redis_mock():
    r = AsyncMock()
    r.xack = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.publish = AsyncMock()
    r.xadd = AsyncMock()
    return r


@pytest.fixture
def processor(redis_mock):
    p = WarmProcessor(redis_mock)
    p.close_window = AsyncMock()  # stub: these tests only check OpenWindow bookkeeping
    return p


def test_window_start_epoch_boundary():
    # window_s=30: [990, 1020) is one window; 1020.0 starts the next.
    t1 = datetime.fromtimestamp(1019.9, tz=timezone.utc)
    t2 = datetime.fromtimestamp(1020.0, tz=timezone.utc)
    assert _window_start_epoch(t1, 30) == 990
    assert _window_start_epoch(t2, 30) == 1020


@pytest.mark.asyncio
async def test_frames_in_same_window_share_window_id(processor, redis_mock):
    base_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    for i in range(5):
        ts = base_ts.replace(second=i)
        raw = make_raw_frame(i, "EXC001", ts)
        await processor.handle_message("telemetry:0", f"{i}-0".encode(), raw)

    assert len(processor.open_windows) == 1
    win = processor.open_windows["EXC001"]
    assert len(win.frames) == 5


@pytest.mark.asyncio
async def test_frame_at_window_boundary_closes_previous_window(processor, redis_mock):
    base_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    # first frame opens window [0, 30)
    await processor.handle_message("telemetry:0", b"1-0", make_raw_frame(1, "EXC001", base_ts))
    # a frame far past window_end + allowed_lateness must close the old window
    late_ts = base_ts.replace(second=0, minute=1)  # 60s later, well past 30+5s
    await processor.handle_message("telemetry:0", b"2-0", make_raw_frame(2, "EXC001", late_ts))

    processor.close_window.assert_awaited_once()
    assert len(processor.open_windows["EXC001"].frames) == 1  # only the new frame


@pytest.mark.asyncio
async def test_duplicate_seq_counted_once(processor, redis_mock):
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    raw = make_raw_frame(5, "EXC001", ts)
    await processor.handle_message("telemetry:0", b"5-0", raw)
    processor.persisted_last_seq["EXC001"] = 5  # simulate this seq already persisted
    await processor.handle_message("telemetry:0", b"5-0-replay", raw)

    win = processor.open_windows["EXC001"]
    assert len(win.frames) == 1  # the replay must not be added again
    redis_mock.xack.assert_awaited()


@pytest.mark.asyncio
async def test_late_frame_increments_late_frames_not_added(processor, redis_mock):
    base_ts = datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc)  # opens window [0,30)
    await processor.handle_message("telemetry:0", b"1-0", make_raw_frame(1, "EXC001", base_ts))

    earlier_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc) - __import__("datetime").timedelta(seconds=1)
    late_raw = make_raw_frame(0, "EXC001", earlier_ts)
    await processor.handle_message("telemetry:0", b"0-0", late_raw)

    win = processor.open_windows["EXC001"]
    assert win.late_frames == 1
    assert len(win.frames) == 1  # the late frame was NOT added to the window
    redis_mock.xack.assert_awaited()


@pytest.mark.asyncio
async def test_unknown_machine_acked_and_ignored(processor, redis_mock):
    raw = make_raw_frame(1, "UNKNOWN_MACHINE", datetime.now(timezone.utc))
    await processor.handle_message("telemetry:0", b"1-0", raw)
    assert "UNKNOWN_MACHINE" not in processor.open_windows
    redis_mock.xack.assert_awaited_once()


@pytest.mark.asyncio
async def test_flush_stale_closes_idle_windows(processor, redis_mock):
    ts = datetime.now(timezone.utc)
    await processor.handle_message("telemetry:0", b"1-0", make_raw_frame(1, "EXC001", ts))
    assert "EXC001" in processor.open_windows

    await processor.flush_stale(warm_idle_flush_s=0)  # everything is "stale" with threshold 0
    processor.close_window.assert_awaited_once()
    assert "EXC001" not in processor.open_windows
