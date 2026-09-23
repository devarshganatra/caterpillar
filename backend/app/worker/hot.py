import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import Event as DBEvent, MachineStateLog
from backend.app.services.stream import parse_flat_context
from contracts.events import (
    TelemetryFrame, ContextFrame, MachineState, Event, UiPush, UiPushType, ProximityReading
)
from contracts.ids import hot_event_id
from contracts.machine_config import MACHINES
from contracts.shard import shard_for
from core.copilot_core.state import classify_state
from core.copilot_core.envelope import calculate_envelope
from core.copilot_core.safety import evaluate_safety
from core.copilot_core.health import evaluate_health
from core.copilot_core.risk import update_risk
from core.copilot_core.arbitrator import arbitrate, AlertState

logger = logging.getLogger(__name__)

# ARCH §6.1: Idle Hub opens after IDLE is held >= 30 s. idle_ticks increments
# once per IDLE frame at (nominally) 1 Hz simulated time, so this is a tick
# count, not a wall-clock duration (wall clock is compressed by SIM_SPEED).
IDLE_HUB_DWELL_TICKS = 30

# How often (wall-clock seconds) to re-read the site's context hash. Context
# only changes on scenario events (rare), so this avoids an HGETALL per frame.
CONTEXT_POLL_INTERVAL_S = 1.0

@dataclass
class MachineHotState:
    machine_id: str
    last_seq: int = -1
    state: MachineState = MachineState.OFF
    dwell_ticks: int = 0
    idle_ticks: int = 0
    risk_score: float = 0.0
    risk_level: str = "NORMAL"
    last_context_snapshot: ContextFrame | None = None
    context_checked_at: float = 0.0
    last_envelope_conditions: list[str] = field(default_factory=list)
    alert_states: dict[str, AlertState] = field(default_factory=dict)

    # Batching. event_buffer holds (frame_seq, event) so each event is
    # persisted with the frame_seq it actually fired on, not the state's
    # last_seq at flush time (multiple frames can be buffered per flush).
    event_buffer: list[tuple[int, Event]] = field(default_factory=list)
    state_log_buffer: list[MachineStateLog] = field(default_factory=list)
    unacked_msg_ids: list[bytes] = field(default_factory=list)
    last_db_flush: float = field(default_factory=time.monotonic)



def parse_flat_redis_telemetry(raw: dict) -> TelemetryFrame:
    # Convert Redis flat dict back to TelemetryFrame
    def _val(k_str, default=None, typ=str):
        k = k_str.encode('utf-8')
        if k not in raw: return default
        v = raw[k]
        if typ == bool: return v == b'true'
        if typ == float: return float(v)
        if typ == int: return int(v)
        return v.decode('utf-8')
        
    ts = datetime.fromisoformat(_val('ts')).replace(tzinfo=timezone.utc)
    
    prox = None
    if _val('proximity_zone'):
        prox = ProximityReading(
            zone=_val('proximity_zone'),
            distance_m=_val('proximity_distance_m', typ=float),
            source=_val('proximity_source')
        )
        
    return TelemetryFrame(
        seq=_val('seq', typ=int),
        ts=ts,
        machine_id=_val('machine_id'),
        operator_id=_val('operator_id'),
        task_id=_val('task_id', default=""),
        engine_rpm=_val('engine_rpm', typ=float),
        engine_temp_c=_val('engine_temp_c', typ=float),
        hydraulic_pressure_bar=_val('hydraulic_pressure_bar', typ=float),
        fuel_rate_lph=_val('fuel_rate_lph', typ=float),
        speed_kmh=_val('speed_kmh', typ=float),
        seatbelt=_val('seatbelt'),
        proximity=prox,
        cycle_completed=_val('cycle_completed', typ=bool),
        truck_present=_val('truck_present', typ=bool),
        hauler_queue_len=_val('hauler_queue_len', typ=int),
        gps=(_val('gps_lat', default=0.0, typ=float), _val('gps_lon', default=0.0, typ=float)),
        sig=_val('sig')
    )


async def flush_buffers(
    session: AsyncSession, 
    machine_id: str, 
    hot_state: MachineHotState,
    redis_client: Redis,
    stream_key: str,
    group_name: str
):
    if not hot_state.event_buffer and not hot_state.state_log_buffer and not hot_state.unacked_msg_ids:
        hot_state.last_db_flush = time.monotonic()
        return

    # 1. Insert events using ON CONFLICT DO NOTHING (idempotent by machine_id, seq, type).
    # Each event carries its OWN frame_seq (the frame it fired on), not the
    # state's last_seq — otherwise multiple frames buffered between flushes
    # collide on (machine_id, frame_seq, type) and silently drop all but one.
    if hot_state.event_buffer:
        values = []
        for seq, e in hot_state.event_buffer:
            values.append({
                "id": UUID(e.event_id),
                "type": e.type,
                "severity": e.severity.value,
                "machine_id": e.machine_id,
                "operator_id": e.operator_id,
                "site_id": e.site_id,
                "ts": e.ts,
                "source_engine": e.source_engine,
                "evidence": e.evidence,
                "frame_seq": seq
            })

        stmt = insert(DBEvent).values(values)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_events_machine_seq_type"
        )
        await session.execute(stmt)
        
    # 2. Insert state logs using ON CONFLICT DO NOTHING
    if hot_state.state_log_buffer:
        log_values = []
        for log in hot_state.state_log_buffer:
            log_values.append({
                "ts": log.ts,
                "machine_id": log.machine_id,
                "state": log.state,
                "risk_score": log.risk_score,
                "risk_level": log.risk_level,
                "frame_seq": log.frame_seq
            })
            
        stmt = insert(MachineStateLog).values(log_values)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["machine_id", "ts"]
        )
        await session.execute(stmt)
        
    await session.commit()

    # 3. Now that events are durably committed, publish them to the events
    # stream for the warm worker / correlator to consume. This happens after
    # commit (so a crash here just means the warm path sees the event a
    # little later via the events stream backlog, never a phantom event that
    # was never actually persisted).
    if hot_state.event_buffer:
        for seq, e in hot_state.event_buffer:
            try:
                await redis_client.xadd(
                    f"events:{shard_for(e.machine_id)}",
                    {"data": e.model_dump_json(), "frame_seq": str(seq)},
                    maxlen=200000, approximate=True
                )
            except Exception as ex:
                logger.error(f"Failed to XADD event {e.event_id} to events stream: {ex}")

    if hot_state.unacked_msg_ids:
        # XACK all messages that were successfully committed to DB
        await redis_client.xack(stream_key, group_name, *hot_state.unacked_msg_ids)

    hot_state.event_buffer.clear()
    hot_state.state_log_buffer.clear()
    hot_state.unacked_msg_ids.clear()
    hot_state.last_db_flush = time.monotonic()


async def process_frame(
    raw_data: dict, 
    msg_id: bytes,
    hot_state: MachineHotState, 
    redis_client: Redis,
    session: AsyncSession,
    stream_key: str,
    group_name: str
):
    try:
        frame = parse_flat_redis_telemetry(raw_data)
    except Exception as e:
        logger.error(f"Failed to parse telemetry frame: {e}")
        return # Skip unparseable

    # Deduplicate (if seq <= last_seq, we already processed it or it's out of order)
    if frame.seq <= hot_state.last_seq and hot_state.last_seq != -1:
        # It's a duplicate, but we need to XACK it so it stops getting redelivered
        hot_state.unacked_msg_ids.append(msg_id)
        return
        
    hot_state.last_seq = frame.seq
    hot_state.unacked_msg_ids.append(msg_id)

    # Site is a static property of the machine config, not a TODO lookup.
    machine_cfg = MACHINES.get(frame.machine_id)
    site_id = machine_cfg.site_id if machine_cfg else "UNKNOWN"

    # 1. Engines
    new_state, new_dwell = classify_state(frame, hot_state.state, hot_state.dwell_ticks)

    if new_state == MachineState.IDLE:
        hot_state.idle_ticks += 1
    else:
        hot_state.idle_ticks = 0

    hot_state.state = new_state
    hot_state.dwell_ticks = new_dwell

    # Refresh the context snapshot from Redis (published by /ingest/context).
    # Throttled to CONTEXT_POLL_INTERVAL_S wall-clock seconds; on any read
    # failure or empty hash, the previous snapshot is kept as-is.
    now_mono = time.monotonic()
    if now_mono - hot_state.context_checked_at >= CONTEXT_POLL_INTERVAL_S:
        hot_state.context_checked_at = now_mono
        try:
            raw_ctx = await redis_client.hgetall(f"context:{site_id}")
            parsed = parse_flat_context(raw_ctx)
            if parsed is not None:
                hot_state.last_context_snapshot = parsed
        except Exception as e:
            logger.warning(f"Failed to read context for site {site_id}: {e}")

    envelope = calculate_envelope(hot_state.last_context_snapshot)

    # Publish an envelope UiPush only when the active conditions actually
    # change, so the HUD isn't re-sent an identical envelope every second.
    if envelope.active_conditions != hot_state.last_envelope_conditions:
        hot_state.last_envelope_conditions = envelope.active_conditions
        envelope_push = UiPush(
            type=UiPushType.envelope,
            machine_id=frame.machine_id,
            ts=frame.ts,
            payload={
                "red_radius_m": envelope.red_radius_m,
                "orange_radius_m": envelope.orange_radius_m,
                "speed_cap_kmh": envelope.speed_cap_kmh,
                "condition_multiplier": envelope.condition_multiplier,
                "active_conditions": envelope.active_conditions,
                "notes": ", ".join(envelope.active_conditions) if envelope.active_conditions else "No restrictions"
            }
        )
        await redis_client.publish(f"ui:{frame.machine_id}", envelope_push.model_dump_json())

    safety_events = evaluate_safety(frame, hot_state.state, envelope, hot_state.idle_ticks, site_id)
    health_events = evaluate_health(frame, site_id)
    raw_events = safety_events + health_events

    # Deterministic event IDs: replace the uuid4() ids the pure engines
    # assign with ids derived from (machine_id, frame_seq, type, evidence).
    # This must happen before arbitrate() so the id an operator acks/sees is
    # the same id that lands in the DB and on the events stream, and so
    # reprocessing this exact frame never mints a new event identity.
    all_events = [
        e.model_copy(update={
            "event_id": hot_event_id(e.machine_id, frame.seq, e.type, e.evidence)
        })
        for e in raw_events
    ]

    new_risk, new_risk_level = update_risk(
        hot_state.risk_score,
        hot_state.risk_level,
        all_events,
        1.0, # assume 1 Hz
        envelope.condition_multiplier
    )
    
    hot_state.risk_score = new_risk
    hot_state.risk_level = new_risk_level
    
    pushes, new_alert_states = arbitrate(
        all_events, 
        hot_state.alert_states, 
        frame.ts
    )
    hot_state.alert_states = new_alert_states
    
    # 2. Buffer for DB (paired with the frame_seq they actually fired on)
    hot_state.event_buffer.extend((frame.seq, e) for e in all_events)
    hot_state.state_log_buffer.append(MachineStateLog(
        ts=frame.ts,
        machine_id=frame.machine_id,
        state=hot_state.state.value,
        risk_score=hot_state.risk_score,
        risk_level=hot_state.risk_level,
        frame_seq=frame.seq
    ))

    # UI mode: HUD while TRAVEL/WORKING/OFF, or IDLE that hasn't dwelled long
    # enough yet; IDLE_HUB once IDLE has been held >= IDLE_HUB_DWELL_TICKS.
    # This drives the frontend's HUD <-> Idle Hub transition (ARCH §6.1).
    ui_mode = "IDLE_HUB" if (
        hot_state.state == MachineState.IDLE and hot_state.idle_ticks >= IDLE_HUB_DWELL_TICKS
    ) else "HUD"

    # 3. Redis State Update
    state_dict = {
        "state": hot_state.state.value,
        "ui_mode": ui_mode,
        "risk_score": str(round(hot_state.risk_score, 1)),
        "risk_level": hot_state.risk_level,
        "last_seq": str(hot_state.last_seq),
        "ts": frame.ts.isoformat()
    }
    await redis_client.hset(f"machine_state:{frame.machine_id}", mapping=state_dict)
    
    # 4. Push to UI
    # Always push state updates
    pushes.append(UiPush(
        type=UiPushType.state_change,
        machine_id=frame.machine_id,
        ts=frame.ts,
        payload=state_dict
    ))
    
    for p in pushes:
        # Pydantic v2 dump
        msg = p.model_dump_json()
        await redis_client.publish(f"ui:{frame.machine_id}", msg)

    # 5. Flush DB if needed
    if time.monotonic() - hot_state.last_db_flush >= 1.0:
        await flush_buffers(session, frame.machine_id, hot_state, redis_client, stream_key, group_name)
