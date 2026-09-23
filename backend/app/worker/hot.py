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
from contracts.events import (
    TelemetryFrame, ContextFrame, MachineState, Event, UiPush, UiPushType, ProximityReading
)
from core.copilot_core.state import classify_state
from core.copilot_core.envelope import calculate_envelope
from core.copilot_core.safety import evaluate_safety
from core.copilot_core.health import evaluate_health
from core.copilot_core.risk import update_risk
from core.copilot_core.arbitrator import arbitrate, AlertState

logger = logging.getLogger(__name__)

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
    alert_states: dict[str, AlertState] = field(default_factory=dict)
    
    # Batching
    event_buffer: list[Event] = field(default_factory=list)
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

    # 1. Insert events using ON CONFLICT DO NOTHING (idempotent by machine_id, seq, type)
    if hot_state.event_buffer:
        values = []
        for e in hot_state.event_buffer:
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
                # Link event to frame sequence for idempotency
                "frame_seq": hot_state.last_seq
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
    
    # If we don't know the site context, try to fetch it
    site_id = "SITE-A" # TODO lookup machine site
    
    # 1. Engines
    new_state, new_dwell = classify_state(frame, hot_state.state, hot_state.dwell_ticks)
    
    if new_state == MachineState.IDLE:
        hot_state.idle_ticks += 1
    else:
        hot_state.idle_ticks = 0
        
    hot_state.state = new_state
    hot_state.dwell_ticks = new_dwell
    
    envelope = calculate_envelope(hot_state.last_context_snapshot)
    
    safety_events = evaluate_safety(frame, hot_state.state, envelope, hot_state.idle_ticks, site_id)
    health_events = evaluate_health(frame, site_id)
    all_events = safety_events + health_events
    
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
    
    # 2. Buffer for DB
    hot_state.event_buffer.extend(all_events)
    hot_state.state_log_buffer.append(MachineStateLog(
        ts=frame.ts,
        machine_id=frame.machine_id,
        state=hot_state.state.value,
        risk_score=hot_state.risk_score,
        risk_level=hot_state.risk_level,
        frame_seq=frame.seq
    ))
    
    # 3. Redis State Update
    state_dict = {
        "state": hot_state.state.value,
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
