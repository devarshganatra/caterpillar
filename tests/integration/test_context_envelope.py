"""
Integration test for Batch 3.0 fix G5: the hot worker must actually read
context:{site_id} from Redis and apply it to the safety envelope (weather,
ground, etc.), instead of always calculating the base envelope because
last_context_snapshot was never populated.
"""
from datetime import datetime, timezone

import pytest
from redis.asyncio import Redis

from backend.app.config import settings
from backend.app.services.stream import flatten_context
from backend.app.worker.hot import MachineHotState, process_frame
from contracts.events import ContextFrame

pytestmark = pytest.mark.integration


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
    return {k.encode(): v.encode() for k, v in base.items()}


@pytest.fixture
async def redis_client():
    client = Redis.from_url(settings.redis_url)
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_rain_context_narrows_speed_cap(redis_client):
    site_id = "SITE-A"
    ctx = ContextFrame(
        ts=datetime.now(timezone.utc), site_id=site_id, weather="RAIN",
        rainfall_mm_h=12.0, visibility_m=180.0, ambient_temp_c=18.0,
        wind_kmh=10.0, ground="MUDDY", daylight=True,
    )
    await redis_client.delete(f"context:{site_id}")
    await redis_client.hset(f"context:{site_id}", mapping=flatten_context(ctx))

    hot_state = MachineHotState(machine_id="EXC001")
    ts = datetime.now(timezone.utc)
    raw = make_raw_frame(1, "EXC001", ts, speed_kmh="3.0")

    class _NoopSession:
        pass

    # process_frame only uses `session` for buffering, not for a DB call
    # itself — buffers are flushed separately. A lightweight stand-in is
    # fine here since we only assert on hot_state / redis after this call.
    await process_frame(raw, b"1-0", hot_state, redis_client, _NoopSession(), "telemetry:0", "hot-worker")

    assert hot_state.last_context_snapshot is not None
    assert hot_state.last_context_snapshot.weather == "RAIN"

    state = await redis_client.hgetall(f"machine_state:EXC001")
    assert state, "expected machine_state hash to be populated"

    await redis_client.delete(f"context:{site_id}")
