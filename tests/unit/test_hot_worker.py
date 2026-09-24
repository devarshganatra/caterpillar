"""
Unit tests for backend.app.worker.hot — no real Redis/Postgres required.
Redis and the DB session are mocked so these run fast and in CI.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.app.worker.hot import MachineHotState, process_frame, IDLE_HUB_DWELL_TICKS
from contracts.events import MachineState

pytestmark = pytest.mark.unit


def make_raw_frame(seq: int, machine_id: str = "EXC001", ts: datetime | None = None, **overrides) -> dict:
    """Builds the flat byte-keyed dict shape process_frame expects (mirrors stream.flatten_telemetry)."""
    ts = ts or datetime.now(timezone.utc)
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
        "truck_present": "false",
        "hauler_queue_len": "0",
        "gps_lat": "37.7749",
        "gps_lon": "-122.4194",
        "sig": "deadbeef",
    }
    base.update(overrides)
    return {k.encode(): v.encode() for k, v in base.items()}


@pytest.fixture
def redis_mock():
    r = AsyncMock()
    r.hset = AsyncMock()
    r.publish = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.xadd = AsyncMock()
    r.xack = AsyncMock()
    return r


@pytest.fixture
def session_mock():
    s = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_site_id_is_looked_up_not_hardcoded(redis_mock, session_mock):
    """G2: SITE-B machine events must carry site_id='SITE-B', not the old hardcoded 'SITE-A'."""
    hot_state = MachineHotState(machine_id="EXC003")  # EXC003 is SITE-B per contracts.machine_config
    raw = make_raw_frame(1, machine_id="EXC003", seatbelt="UNFASTENED", speed_kmh="3.5")
    await process_frame(raw, b"1-0", hot_state, redis_mock, session_mock, "telemetry:0", "hot-worker")

    assert hot_state.event_buffer, "expected at least one buffered event"
    for seq, event in hot_state.event_buffer:
        assert event.site_id == "SITE-B"


@pytest.mark.asyncio
async def test_deterministic_event_ids_across_replays(redis_mock, session_mock):
    """G4: processing the identical frame twice must produce the identical event_id."""
    ts = datetime.now(timezone.utc)

    state1 = MachineHotState(machine_id="EXC001", state=MachineState.WORKING)
    raw = make_raw_frame(5, ts=ts, seatbelt="UNFASTENED", hydraulic_pressure_bar="250.0")
    await process_frame(raw, b"5-0", state1, redis_mock, session_mock, "telemetry:0", "hot-worker")
    ids_first = sorted(e.event_id for _, e in state1.event_buffer)

    state2 = MachineHotState(machine_id="EXC001", state=MachineState.WORKING)
    await process_frame(raw, b"5-0", state2, redis_mock, session_mock, "telemetry:0", "hot-worker")
    ids_second = sorted(e.event_id for _, e in state2.event_buffer)

    assert ids_first, "expected a SEATBELT_VIOLATION event"
    assert ids_first == ids_second


@pytest.mark.asyncio
async def test_events_keep_their_own_frame_seq(redis_mock, session_mock):
    """
    G3: two different frames that each raise a SEATBELT_VIOLATION must be
    buffered against their OWN frame_seq. Before the fix, flush_buffers used
    hot_state.last_seq for every buffered event, so frame 10's event and
    frame 11's event both got persisted with frame_seq=11, colliding on the
    (machine_id, frame_seq, type) uniqueness constraint and dropping one.
    """
    hot_state = MachineHotState(machine_id="EXC001", state=MachineState.WORKING)

    raw1 = make_raw_frame(10, seatbelt="UNFASTENED")
    await process_frame(raw1, b"10-0", hot_state, redis_mock, session_mock, "telemetry:0", "hot-worker")

    raw2 = make_raw_frame(11, seatbelt="UNFASTENED")
    await process_frame(raw2, b"11-0", hot_state, redis_mock, session_mock, "telemetry:0", "hot-worker")

    seatbelt_events = [(seq, e) for seq, e in hot_state.event_buffer if e.type == "SEATBELT_VIOLATION"]
    assert len(seatbelt_events) == 2, "expected one SEATBELT_VIOLATION per frame"
    seqs = sorted(seq for seq, _ in seatbelt_events)
    assert seqs == [10, 11], f"each event must keep its own frame_seq, got {seqs}"


@pytest.mark.asyncio
async def test_ui_mode_idle_hub_after_dwell(redis_mock, session_mock, monkeypatch):
    """G6: ui_mode flips to IDLE_HUB only after IDLE_HUB_DWELL_TICKS consecutive IDLE frames."""
    # The IDLE_HUB transition also triggers a Stage 4B lesson-delivery
    # check, which deliberately opens its OWN real DB session (see
    # hot._maybe_deliver_lesson's docstring) — stub it here so this stays
    # a real unit test (no infra) and keeps testing only the G6 behavior.
    monkeypatch.setattr("backend.app.worker.hot._maybe_deliver_lesson", AsyncMock(return_value=None))
    hot_state = MachineHotState(machine_id="EXC001")
    ts = datetime.now(timezone.utc)

    last_state_dict = None
    for i in range(IDLE_HUB_DWELL_TICKS + 5):
        raw = make_raw_frame(
            i, ts=ts,
            engine_rpm="850.0", hydraulic_pressure_bar="10.0", speed_kmh="0.0",
        )
        await process_frame(raw, f"{i}-0".encode(), hot_state, redis_mock, session_mock, "telemetry:0", "hot-worker")
        last_state_dict = redis_mock.hset.call_args.kwargs["mapping"]

    assert hot_state.state == MachineState.IDLE
    assert last_state_dict["ui_mode"] == "IDLE_HUB"


@pytest.mark.asyncio
async def test_ui_mode_stays_hud_while_working(redis_mock, session_mock):
    hot_state = MachineHotState(machine_id="EXC001")
    raw = make_raw_frame(1, hydraulic_pressure_bar="250.0")  # >=120 -> WORKING
    await process_frame(raw, b"1-0", hot_state, redis_mock, session_mock, "telemetry:0", "hot-worker")
    state_dict = redis_mock.hset.call_args.kwargs["mapping"]
    assert state_dict["ui_mode"] == "HUD"


@pytest.mark.asyncio
async def test_context_read_changes_envelope_and_publishes(redis_mock, session_mock):
    """G5: a RAIN context snapshot must be picked up and change the envelope, with a push emitted."""
    rain_ctx = {
        b"ts": datetime.now(timezone.utc).isoformat().encode(),
        b"site_id": b"SITE-A",
        b"weather": b"RAIN",
        b"rainfall_mm_h": b"12.0",
        b"visibility_m": b"180.0",
        b"ambient_temp_c": b"18.0",
        b"wind_kmh": b"10.0",
        b"ground": b"MUDDY",
        b"daylight": b"true",
    }
    redis_mock.hgetall = AsyncMock(return_value=rain_ctx)

    hot_state = MachineHotState(machine_id="EXC001", state=MachineState.TRAVEL)
    raw = make_raw_frame(1, speed_kmh="3.0")
    await process_frame(raw, b"1-0", hot_state, redis_mock, session_mock, "telemetry:0", "hot-worker")

    assert hot_state.last_context_snapshot is not None
    assert hot_state.last_context_snapshot.weather == "RAIN"
    assert "RAIN" in hot_state.last_envelope_conditions

    published_types = [
        __import__("json").loads(call.args[1])["type"]
        for call in redis_mock.publish.call_args_list
    ]
    assert "envelope" in published_types


@pytest.mark.asyncio
async def test_no_context_keeps_base_envelope_no_duplicate_push(redis_mock, session_mock):
    hot_state = MachineHotState(machine_id="EXC001")
    raw = make_raw_frame(1)
    await process_frame(raw, b"1-0", hot_state, redis_mock, session_mock, "telemetry:0", "hot-worker")
    # No context -> active_conditions stays [] -> no change -> no envelope push
    published_types = [
        __import__("json").loads(call.args[1])["type"]
        for call in redis_mock.publish.call_args_list
    ]
    assert "envelope" not in published_types


@pytest.mark.asyncio
async def test_duplicate_seq_is_skipped(redis_mock, session_mock):
    hot_state = MachineHotState(machine_id="EXC001", last_seq=5)
    raw = make_raw_frame(5)
    await process_frame(raw, b"5-0", hot_state, redis_mock, session_mock, "telemetry:0", "hot-worker")
    assert hot_state.event_buffer == []
    assert hot_state.state_log_buffer == []
    assert b"5-0" in hot_state.unacked_msg_ids
