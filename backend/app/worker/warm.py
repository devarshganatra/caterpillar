"""
Warm worker (Stage 3 Batch 3D): consumes telemetry:{shard} on its OWN
consumer group ("warm-worker", separate from the hot worker's "hot-worker"
group on the same streams), aggregates 30s event-time windows per machine,
runs ETA / idle attribution / anomaly detection, persists results, emits
Events to events:{shard} for the correlator (Batch 3E), and pushes UI
updates. Never writes to machine_state:{id} or touches risk/arbitration —
the hot path stays the sole safety authority.

Run: PYTHONPATH=. python -m backend.app.worker.warm
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import (
    Event as DBEvent, WindowAggregate, EtaEstimateRow, Task, User,
)
from backend.app.services import eta as eta_service
from backend.app.services.attribution import classify_idle_frames, attribute_window, operator_deviation
from backend.app.services.anomaly import score_window
from backend.app.services.ml_registry import get_registry
from backend.app.services.stream import parse_flat_context
from backend.app.worker.hot import parse_flat_redis_telemetry
from contracts.events import Event, AlertSeverity, UiPush, UiPushType
from contracts.ids import warm_event_id, eta_slip_event_id
from contracts.intelligence import EtaEstimate, EtaFactor
from contracts.machine_config import MACHINES
from contracts.shard import shard_for
from ml.features import compute_window_features

logger = logging.getLogger(__name__)

GROUP_NAME = "warm-worker"
N_SHARDS = 4


def _window_start_epoch(ts: datetime, window_s: int) -> int:
    return int(ts.timestamp() // window_s) * window_s


def _parse_planned_breaks(raw: str) -> list[tuple[str, str]]:
    if not raw.strip():
        return []
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part or "-" not in part:
            continue
        start, end = part.split("-", 1)
        out.append((start.strip(), end.strip()))
    return out


@dataclass
class OpenWindow:
    window_id: str
    machine_id: str
    start: datetime
    end: datetime
    frames: list = field(default_factory=list)         # list[TelemetryFrame]
    msg_ids: list = field(default_factory=list)         # list[(stream_key, bytes)]
    late_frames: int = 0
    last_activity_wall: float = field(default_factory=time.monotonic)


@dataclass
class _BaselineEta:
    p10_min: float
    p50_min: float
    p90_min: float
    model_version: str | None
    factors: list[EtaFactor]
    base_value_min: float | None
    defaults_used: list[str]


class WarmProcessor:
    """
    Owns all in-process state for window aggregation. No infinite loop here —
    main() below drives it. Kept this way so tests can call handle_message /
    close_window / flush_stale / recover directly without a live stream.
    """

    def __init__(self, redis_client: Redis):
        self.redis = redis_client
        self.open_windows: dict[str, OpenWindow] = {}
        self.persisted_last_seq: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Ingestion
    # ------------------------------------------------------------------ #

    async def recover(self) -> None:
        """Restores persisted_last_seq from the DB (restart-safety: an
        already-closed window's frames must never be re-added to a new one)."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WindowAggregate.machine_id, func.max(WindowAggregate.last_seq))
                .group_by(WindowAggregate.machine_id)
            )
            for machine_id, max_seq in result.all():
                if max_seq is not None:
                    self.persisted_last_seq[machine_id] = max_seq
        logger.info(f"warm recover(): persisted_last_seq={self.persisted_last_seq}")

    def _new_window(self, machine_id: str, ts: datetime) -> OpenWindow:
        window_s = settings.warm_window_seconds
        start_epoch = _window_start_epoch(ts, window_s)
        start = datetime.fromtimestamp(start_epoch, tz=timezone.utc)
        end = start + timedelta(seconds=window_s)
        return OpenWindow(window_id=f"{machine_id}:{start_epoch}", machine_id=machine_id, start=start, end=end)

    async def handle_message(self, stream_key: str, msg_id: bytes, raw_data: dict) -> None:
        try:
            frame = parse_flat_redis_telemetry(raw_data)
        except Exception as e:
            logger.error(f"warm: failed to parse telemetry frame: {e}")
            await self.redis.xack(stream_key, GROUP_NAME, msg_id)
            return

        machine_id = frame.machine_id
        if machine_id not in MACHINES:
            await self.redis.xack(stream_key, GROUP_NAME, msg_id)
            return

        # Idempotency: a frame whose seq is already reflected in a persisted
        # window (recover() or an earlier close_window() in this process)
        # is a replay — ack and drop, never re-add to a window.
        last_seq = self.persisted_last_seq.get(machine_id, -1)
        if frame.seq <= last_seq:
            await self.redis.xack(stream_key, GROUP_NAME, msg_id)
            return

        open_win = self.open_windows.get(machine_id)
        if open_win is None:
            open_win = self._new_window(machine_id, frame.ts)
            self.open_windows[machine_id] = open_win

        if frame.ts < open_win.start:
            # Arrived after its own window's boundary already advanced.
            open_win.late_frames += 1
            logger.warning(f"warm: late_dropped machine={machine_id} seq={frame.seq} ts={frame.ts} window={open_win.window_id}")
            await self.redis.xack(stream_key, GROUP_NAME, msg_id)
            return

        if frame.ts >= open_win.end + timedelta(seconds=settings.warm_allowed_lateness_s):
            del self.open_windows[machine_id]
            await self.close_window(open_win)
            open_win = self._new_window(machine_id, frame.ts)
            self.open_windows[machine_id] = open_win

        open_win.frames.append(frame)
        open_win.msg_ids.append((stream_key, msg_id))
        open_win.last_activity_wall = time.monotonic()

    async def flush_stale(self, warm_idle_flush_s: int | None = None) -> None:
        """Closes windows whose machine has sent nothing for warm_idle_flush_s
        wall-clock seconds (e.g. the machine went OFF or the simulator stopped)."""
        threshold = warm_idle_flush_s if warm_idle_flush_s is not None else settings.warm_idle_flush_s
        now = time.monotonic()
        stale = [mid for mid, w in self.open_windows.items() if now - w.last_activity_wall >= threshold]
        for machine_id in stale:
            open_win = self.open_windows.pop(machine_id)
            await self.close_window(open_win)

    # ------------------------------------------------------------------ #
    # Window close pipeline
    # ------------------------------------------------------------------ #

    async def close_window(self, open_win: OpenWindow) -> None:
        t0 = time.monotonic()
        machine_id = open_win.machine_id
        frames = open_win.frames
        frame_count = len(frames)

        if frame_count == 0:
            return  # nothing arrived in this window at all; not persisted

        seqs = [f.seq for f in frames]
        first_seq, last_seq = min(seqs), max(seqs)
        site_id = MACHINES[machine_id].site_id
        operator_id = frames[-1].operator_id
        task_id = frames[-1].task_id or None

        events_to_emit: list[Event] = []
        inference_status = "OK"

        try:
            async with AsyncSessionLocal() as session:
                if frame_count < settings.warm_min_frames:
                    await self._insert_window_row(session, self._minimal_row(
                        open_win, first_seq, last_seq, frame_count, site_id, operator_id, task_id,
                    ))
                    await session.commit()
                    idle_push_payload = None
                    anomaly_push_payload = None
                    eta_push_payload = None
                else:
                    row, events_to_emit, idle_push_payload, anomaly_push_payload, eta_push_payload = \
                        await self._process_full_window(
                            session, open_win, frames, first_seq, last_seq, frame_count,
                            site_id, operator_id, task_id,
                        )
                    await self._insert_window_row(session, row)
                    for e in events_to_emit:
                        await self._insert_event(session, e, last_seq)
                    await session.commit()
        except Exception as e:
            inference_status = "ERROR"
            logger.error(f"warm: close_window failed for {open_win.window_id}, leaving messages pending: {e}")
            # No XACK on failure: messages stay pending and are retried on
            # the next XREADGROUP/restart (idempotency guarantees no double
            # DB effect when they are eventually reprocessed).
            return

        for e in events_to_emit:
            try:
                await self.redis.xadd(
                    f"events:{shard_for(e.machine_id)}",
                    {"data": e.model_dump_json(), "frame_seq": str(last_seq)},
                    maxlen=200000, approximate=True,
                )
            except Exception as ex:
                logger.error(f"warm: failed to XADD event {e.event_id}: {ex}")

        if idle_push_payload is not None:
            await self._publish(machine_id, UiPushType.idle_attribution, open_win.end, idle_push_payload)
        if anomaly_push_payload is not None:
            await self._publish(machine_id, UiPushType.anomaly, open_win.end, anomaly_push_payload)
        if eta_push_payload is not None:
            await self._publish(machine_id, UiPushType.eta, open_win.end, eta_push_payload)

        for stream_key, msg_id in open_win.msg_ids:
            await self.redis.xack(stream_key, GROUP_NAME, msg_id)

        self.persisted_last_seq[machine_id] = last_seq
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        await self._update_worker_status(latency_ms)
        logger.info(json.dumps({
            "service": "warm", "window_id": open_win.window_id, "machine_id": machine_id,
            "frame_count": frame_count, "late_frames": open_win.late_frames,
            "latency_ms": latency_ms, "inference_status": inference_status,
        }))

    async def _process_full_window(
        self, session: AsyncSession, open_win: OpenWindow, frames: list, first_seq: int, last_seq: int,
        frame_count: int, site_id: str, operator_id: str, task_id: str | None,
    ):
        machine_id = open_win.machine_id
        registry = get_registry()

        frames_dict = self._build_frames_dict(frames)
        features = compute_window_features(frames_dict, sim_seconds_per_frame=settings.sim_seconds_per_frame)

        raw_ctx = await self.redis.hgetall(f"context:{site_id}")
        ctx_frame = parse_flat_context(raw_ctx)
        context_dict = ctx_frame.model_dump(mode="json") if ctx_frame else {}

        alerts_count = await self._count_alerts(session, machine_id, open_win.start, open_win.end)
        fault_event_ids, fault_active = await self._fault_signals(session, machine_id, frames, open_win)

        cfg = self._attribution_cfg()
        causes = classify_idle_frames(
            n_frames=frame_count, ts_start=frames[0].ts, sim_seconds_per_frame=settings.sim_seconds_per_frame,
            engine_temp_c=frames_dict["engine_temp_c"], hydraulic_pressure_bar=frames_dict["hydraulic_pressure_bar"],
            truck_present=frames_dict["truck_present"], hauler_queue_len=frames_dict["hauler_queue_len"],
            context=context_dict, planned_breaks_utc=_parse_planned_breaks(settings.planned_breaks_utc),
            fault_active=fault_active, cfg=cfg,
        )
        attr = attribute_window(
            window_id=open_win.window_id, machine_id=machine_id, operator_id=operator_id,
            window_start=open_win.start, window_end=open_win.end, causes=causes,
            sim_seconds_per_frame=settings.sim_seconds_per_frame,
            truck_present_ratio=features.get("truck_present_ratio") or 0.0,
            hauler_queue_mean=features.get("hauler_queue_mean") or 0.0,
            context=context_dict, fault_event_ids=fault_event_ids,
            planned_break=bool(np.any(causes == "PLANNED")),
        )

        prev_row = await self._previous_window_row(session, machine_id, open_win.start)
        prev_consecutive = (prev_row.deviation_consecutive if (prev_row and prev_row.deviation_raw_flag) else 0)

        operator_skill = await self._operator_skill(session, operator_id)
        task_type_for_dev = "NONE"
        if task_id:
            task_row = await session.get(Task, task_id)
            if task_row and task_row.task_type:
                task_type_for_dev = task_row.task_type

        deviation = operator_deviation(
            operator_idle_ratio=attr.operator_idle_ratio, operator_id=operator_id,
            task_type=task_type_for_dev, weather=context_dict.get("weather", "SUNNY"),
            operator_skill=operator_skill, prev_consecutive_raw_flags=prev_consecutive,
            cfg=cfg, registry=registry,
        )
        attr.deviation_status = deviation.status
        attr.expected_idle_ratio = deviation.expected_idle_ratio
        attr.deviation_ratio = deviation.deviation_ratio
        attr.robust_z = deviation.robust_z
        attr.baseline_level = deviation.baseline_level
        attr.operator_deviation_flag = deviation.operator_deviation_flag
        attr.consecutive_windows = deviation.consecutive_windows

        anomaly_result = score_window(open_win.window_id, machine_id, features, registry)

        eta_payload = None
        eta_events_extra = []
        eta_slip_band = 0
        if task_id:
            eta_payload, eta_events_extra, eta_slip_band = await self._compute_eta(
                session, task_id, machine_id, open_win, features, operator_skill, context_dict, registry,
            )

        events_to_emit: list[Event] = []
        if anomaly_result.is_anomalous:
            events_to_emit.append(self._make_event(
                event_type="OPERATIONAL_ANOMALY", severity=AlertSeverity.WARNING,
                machine_id=machine_id, operator_id=operator_id, site_id=site_id, ts=open_win.end,
                source_engine=f"anomaly@{anomaly_result.model_version or anomaly_result.method}",
                evidence={
                    "window_id": open_win.window_id,
                    "window_start": open_win.start.isoformat(), "window_end": open_win.end.isoformat(),
                    "score": anomaly_result.score, "threshold": anomaly_result.threshold,
                    "method": anomaly_result.method, "model_version": anomaly_result.model_version,
                    "drivers": [d.model_dump() for d in anomaly_result.drivers],
                },
            ))
        if deviation.operator_deviation_flag:
            events_to_emit.append(self._make_event(
                event_type="IDLE_DEVIATION", severity=AlertSeverity.INFO,
                machine_id=machine_id, operator_id=operator_id, site_id=site_id, ts=open_win.end,
                source_engine="attribution@1.0",
                evidence={
                    "window_id": open_win.window_id, "operator_idle_ratio": attr.operator_idle_ratio,
                    "expected_idle_ratio": attr.expected_idle_ratio, "deviation_ratio": attr.deviation_ratio,
                    "robust_z": attr.robust_z, "baseline_level": attr.baseline_level,
                    "consecutive_windows": attr.consecutive_windows, "breakdown_s": attr.breakdown_s,
                },
            ))
        events_to_emit.extend(eta_events_extra)

        row = self._full_row(
            open_win, first_seq, last_seq, frame_count, site_id, operator_id, task_id,
            features, alerts_count, context_dict, attr, anomaly_result, deviation,
            eta_slip_band,
        )

        idle_push = attr.model_dump(mode="json")
        anomaly_push = anomaly_result.model_dump(mode="json")
        eta_push = eta_payload.model_dump(mode="json") if eta_payload else None

        return row, events_to_emit, idle_push, anomaly_push, eta_push

    # ------------------------------------------------------------------ #
    # ETA
    # ------------------------------------------------------------------ #

    async def _compute_eta(
        self, session: AsyncSession, task_id: str, machine_id: str, open_win: OpenWindow,
        features: dict, operator_skill: str, context_dict: dict, registry,
    ) -> tuple[EtaEstimate | None, list[Event], int]:
        task_row = await session.get(Task, task_id)
        if task_row is None:
            return EtaEstimate(task_id=task_id, machine_id=machine_id, window_id=open_win.window_id,
                                ts=open_win.end, status="UNAVAILABLE", unavailable_reason="NO_TASK"), [], 0
        if not task_row.task_type or not task_row.target_cycles:
            return EtaEstimate(task_id=task_id, machine_id=machine_id, window_id=open_win.window_id,
                                ts=open_win.end, status="UNAVAILABLE", unavailable_reason="TASK_METADATA_MISSING"), [], 0

        machine_cfg = MACHINES[machine_id]
        task_ctx = dict(
            task_type=task_row.task_type, machine_type=machine_cfg.type, machine_age_years=machine_cfg.age_years,
            operator_skill=operator_skill, target_cycles=task_row.target_cycles,
            weather=context_dict.get("weather"), rainfall_mm_h=context_dict.get("rainfall_mm_h"),
            visibility_m=context_dict.get("visibility_m"), wind_kmh=context_dict.get("wind_kmh"),
            ambient_temp_c=context_dict.get("ambient_temp_c"), ground=context_dict.get("ground"),
            hauler_queue_mean=features.get("hauler_queue_mean"),
        )

        result = await session.execute(
            select(EtaEstimateRow).where(EtaEstimateRow.task_id == task_id, EtaEstimateRow.kind == "BASELINE")
        )
        baseline_row = result.scalar_one_or_none()

        if baseline_row is None:
            pred = eta_service.predict_task(task_ctx, registry)
            if pred is None:
                return EtaEstimate(task_id=task_id, machine_id=machine_id, window_id=open_win.window_id,
                                    ts=open_win.end, status="UNAVAILABLE", unavailable_reason="MODEL_UNAVAILABLE"), [], 0
            baseline = _BaselineEta(pred.p10_min, pred.p50_min, pred.p90_min, pred.model_version,
                                     pred.factors, pred.base_value_min, pred.defaults_used)
            baseline_payload = EtaEstimate(
                task_id=task_id, machine_id=machine_id, window_id=None, ts=open_win.end, status="OK",
                model_version=baseline.model_version,
                baseline_p10_min=baseline.p10_min, baseline_p50_min=baseline.p50_min, baseline_p90_min=baseline.p90_min,
                planner_estimate_min=task_row.est_duration_minutes,
                factors=baseline.factors, base_value_min=baseline.base_value_min, defaults_used=baseline.defaults_used,
            )
            await self._insert_eta_row(session, task_id, machine_id, None, "BASELINE",
                                        baseline_payload, baseline.model_version)
        else:
            p = baseline_row.payload
            baseline = _BaselineEta(
                p["baseline_p10_min"], p["baseline_p50_min"], p["baseline_p90_min"], baseline_row.model_version,
                [EtaFactor(**f) for f in p.get("factors", [])], p.get("base_value_min"), p.get("defaults_used", []),
            )

        result = await session.execute(
            select(
                func.coalesce(func.sum(WindowAggregate.cycle_count), 0),
                func.coalesce(func.sum(WindowAggregate.frame_count), 0),
            ).where(WindowAggregate.task_id == task_id)
        )
        prior_cycles, prior_frames = result.one()
        cycles_done = int(prior_cycles or 0) + int(features.get("cycle_count") or 0)
        frames_done = int(prior_frames or 0) + int(features.get("frame_count") or 0)
        observed_cycle_s = (frames_done * settings.sim_seconds_per_frame / cycles_done) if cycles_done > 0 else None
        elapsed_min = frames_done * settings.sim_seconds_per_frame / 60.0

        live = eta_service.blend(
            baseline.p50_min, baseline.p10_min, baseline.p90_min, elapsed_min,
            cycles_done, task_row.target_cycles, observed_cycle_s, settings.eta_min_cycles_for_blend,
        )

        prev_row = await self._previous_window_row(session, machine_id, open_win.start)
        last_band = prev_row.last_eta_slip_band if prev_row else 0
        slip_pct, band, severity = eta_service.slip(
            baseline.p50_min, live.eta_total_p50_min, last_band,
            settings.eta_slip_threshold, settings.eta_slip_warning_threshold,
        )

        live_payload = EtaEstimate(
            task_id=task_id, machine_id=machine_id, window_id=open_win.window_id, ts=open_win.end, status="OK",
            model_version=baseline.model_version,
            baseline_p10_min=baseline.p10_min, baseline_p50_min=baseline.p50_min, baseline_p90_min=baseline.p90_min,
            remaining_p10_min=live.remaining_p10_min, remaining_p50_min=live.remaining_p50_min,
            remaining_p90_min=live.remaining_p90_min, eta_total_p50_min=live.eta_total_p50_min,
            progress=live.progress, blend_weight_model=live.blend_weight_model,
            cycles_done=cycles_done, target_cycles=task_row.target_cycles,
            planner_estimate_min=task_row.est_duration_minutes,
            factors=baseline.factors, base_value_min=baseline.base_value_min, defaults_used=baseline.defaults_used,
            slip_pct=slip_pct,
        )
        await self._insert_eta_row(session, task_id, machine_id, open_win.window_id, "LIVE",
                                    live_payload, baseline.model_version)

        events = []
        if severity is not None:
            events.append(self._make_event(
                event_type="ETA_SLIP", severity=AlertSeverity[severity], machine_id=machine_id,
                operator_id="", site_id=MACHINES[machine_id].site_id, ts=open_win.end,
                source_engine=f"eta@{baseline.model_version or 'unknown'}",
                evidence={
                    "task_id": task_id, "window_id": open_win.window_id,
                    "baseline_p50_min": baseline.p50_min, "eta_total_p50_min": live.eta_total_p50_min,
                    "slip_pct": slip_pct, "band": band, "model_version": baseline.model_version,
                },
            ))
        # band is returned (not stashed on self) so it can be persisted onto
        # this window's own row via _full_row's explicit parameter — a
        # shared instance attribute here would race across machines, since
        # different machines' close_window() calls run concurrently as
        # separate asyncio tasks against the same WarmProcessor.
        return live_payload, events, band

    # ------------------------------------------------------------------ #
    # DB helpers
    # ------------------------------------------------------------------ #

    def _build_frames_dict(self, frames: list) -> dict:
        return {
            "engine_rpm": np.array([f.engine_rpm for f in frames]),
            "engine_temp_c": np.array([f.engine_temp_c for f in frames]),
            "hydraulic_pressure_bar": np.array([f.hydraulic_pressure_bar for f in frames]),
            "fuel_rate_lph": np.array([f.fuel_rate_lph for f in frames]),
            "cycle_completed": np.array([f.cycle_completed for f in frames], dtype=bool),
            # Non-authoritative: derived from hydraulic pressure, mirroring
            # core.copilot_core.state's WORKING threshold, only for the
            # idle_ratio feature — never used for safety decisions.
            "is_idle": np.array([f.hydraulic_pressure_bar < 50 and f.speed_kmh < 0.5 for f in frames], dtype=bool),
            "truck_present": np.array([f.truck_present for f in frames], dtype=bool),
            "hauler_queue_len": np.array([f.hauler_queue_len for f in frames], dtype=float),
        }

    async def _count_alerts(self, session: AsyncSession, machine_id: str, start: datetime, end: datetime) -> int:
        result = await session.execute(
            select(func.count()).select_from(DBEvent).where(
                DBEvent.machine_id == machine_id, DBEvent.ts >= start, DBEvent.ts < end,
                ~DBEvent.source_engine.like("warm%"),
            )
        )
        return int(result.scalar_one())

    async def _fault_signals(self, session: AsyncSession, machine_id: str, frames: list, open_win: OpenWindow):
        lookback = open_win.start - timedelta(seconds=settings.machine_fault_hold_s)
        result = await session.execute(
            select(DBEvent.id, DBEvent.ts).where(
                DBEvent.machine_id == machine_id, DBEvent.type == "HEALTH_THRESHOLD",
                DBEvent.ts >= lookback, DBEvent.ts < open_win.end,
            )
        )
        rows = result.all()
        fault_event_ids = [str(r[0]) for r in rows]
        fault_ts = [r[1] for r in rows]

        hold = timedelta(seconds=settings.machine_fault_hold_s)
        fault_active = np.zeros(len(frames), dtype=bool)
        if fault_ts:
            for i, f in enumerate(frames):
                for ts in fault_ts:
                    if ts <= f.ts <= ts + hold:
                        fault_active[i] = True
                        break
        return fault_event_ids, fault_active

    async def _previous_window_row(self, session: AsyncSession, machine_id: str, before: datetime):
        result = await session.execute(
            select(WindowAggregate).where(
                WindowAggregate.machine_id == machine_id, WindowAggregate.window_start < before,
            ).order_by(WindowAggregate.window_start.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def _operator_skill(self, session: AsyncSession, operator_id: str) -> str:
        try:
            uid = uuid.UUID(operator_id)
        except (ValueError, AttributeError):
            return "INTERMEDIATE"
        result = await session.execute(select(User.skill_level).where(User.id == uid))
        val = result.scalar_one_or_none()
        return val or "INTERMEDIATE"

    def _attribution_cfg(self) -> dict:
        return {
            "weather_stop_rain_mm_h": settings.weather_stop_rain_mm_h,
            "weather_stop_visibility_m": settings.weather_stop_visibility_m,
            "weather_stop_wind_kmh": settings.weather_stop_wind_kmh,
            "idle_dev_ratio": settings.idle_dev_ratio,
            "idle_dev_robust_z": settings.idle_dev_robust_z,
            "idle_dev_persist_windows": settings.idle_dev_persist_windows,
            "idle_baseline_min_n": settings.idle_baseline_min_n,
        }

    def _minimal_row(self, open_win, first_seq, last_seq, frame_count, site_id, operator_id, task_id) -> dict:
        return {
            "window_id": open_win.window_id, "machine_id": open_win.machine_id, "site_id": site_id,
            "operator_id": operator_id, "task_id": task_id,
            "window_start": open_win.start, "window_end": open_win.end,
            "frame_count": frame_count, "first_seq": first_seq, "last_seq": last_seq,
            "late_frames": open_win.late_frames,
            "anomaly": {"method": "SKIPPED", "reason": "INSUFFICIENT_FRAMES", "is_anomalous": False},
        }

    def _full_row(
        self, open_win, first_seq, last_seq, frame_count, site_id, operator_id, task_id,
        features, alerts_count, context_dict, attr, anomaly_result, deviation, eta_slip_band,
    ) -> dict:
        row = {
            "window_id": open_win.window_id, "machine_id": open_win.machine_id, "site_id": site_id,
            "operator_id": operator_id, "task_id": task_id,
            "window_start": open_win.start, "window_end": open_win.end,
            "frame_count": frame_count, "first_seq": first_seq, "last_seq": last_seq,
            "late_frames": open_win.late_frames,
            "alerts_count": alerts_count, "context": context_dict,
            "idle_attribution": attr.model_dump(mode="json"),
            "anomaly": anomaly_result.model_dump(mode="json"),
            "deviation_raw_flag": deviation.raw_flag,
            "deviation_consecutive": deviation.consecutive_windows,
            "last_eta_slip_band": eta_slip_band,
        }
        for key in ("fuel_per_cycle", "rpm_mean", "rpm_std", "hyd_p95", "idle_ratio", "cycle_count",
                    "cycle_time_mean", "cycle_time_cv", "temp_slope", "working_ratio",
                    "truck_present_ratio", "hauler_queue_mean", "fuel_l_total"):
            row[key] = features.get(key)
        return row

    async def _insert_window_row(self, session: AsyncSession, row: dict) -> None:
        stmt = pg_insert(WindowAggregate).values(**row).on_conflict_do_nothing(index_elements=["window_id"])
        await session.execute(stmt)

    async def _insert_event(self, session: AsyncSession, e: Event, frame_seq: int) -> None:
        # frame_seq = the last telemetry frame_seq of the window this event
        # was computed from (not the frame that "caused" it — warm events
        # come from a whole window, not one frame). This must be the ACTUAL
        # last_seq, not a constant like 0: the uq_events_machine_seq_type
        # constraint is (machine_id, frame_seq, type), so a constant would
        # make every window's OPERATIONAL_ANOMALY event for a machine
        # collide with the first one ever inserted and silently vanish.
        # last_seq is strictly increasing per machine across windows, so
        # this also gives correct idempotency: replaying the same window's
        # events lands on the same (machine_id, frame_seq, type) as before.
        stmt = pg_insert(DBEvent).values(
            id=uuid.UUID(e.event_id), type=e.type, severity=e.severity.value,
            machine_id=e.machine_id, operator_id=e.operator_id, site_id=e.site_id, ts=e.ts,
            source_engine=e.source_engine, evidence=e.evidence, frame_seq=frame_seq,
        )
        stmt = stmt.on_conflict_do_nothing()
        await session.execute(stmt)

    async def _insert_eta_row(self, session: AsyncSession, task_id, machine_id, window_id, kind,
                               payload: EtaEstimate, model_version: str | None) -> None:
        stmt = pg_insert(EtaEstimateRow).values(
            task_id=task_id, machine_id=machine_id, window_id=window_id, ts=payload.ts,
            kind=kind, payload=payload.model_dump(mode="json"), model_version=model_version,
        )
        if kind == "BASELINE":
            stmt = stmt.on_conflict_do_nothing(index_elements=["task_id"], index_where=(EtaEstimateRow.kind == "BASELINE"))
        else:
            stmt = stmt.on_conflict_do_nothing(constraint="uq_eta_task_window_kind")
        await session.execute(stmt)

    def _make_event(self, event_type, severity, machine_id, operator_id, site_id, ts, source_engine, evidence) -> Event:
        window_id = evidence.get("window_id", "")
        return Event(
            event_id=warm_event_id(window_id, event_type) if event_type != "ETA_SLIP"
                     else eta_slip_event_id(evidence.get("task_id", ""), evidence.get("band", 0)),
            type=event_type, severity=severity, machine_id=machine_id, operator_id=operator_id,
            site_id=site_id, ts=ts, source_engine=source_engine, evidence=evidence,
        )

    async def _publish(self, machine_id: str, push_type: UiPushType, ts: datetime, payload: dict) -> None:
        push = UiPush(type=push_type, machine_id=machine_id, ts=ts, payload=payload)
        await self.redis.publish(f"ui:{machine_id}", push.model_dump_json())

    async def _update_worker_status(self, latency_ms: float) -> None:
        try:
            await self.redis.hset("worker:status:warm", mapping={
                "last_ok_ts": datetime.now(timezone.utc).isoformat(),
                "last_latency_ms": str(latency_ms),
            })
        except Exception:
            pass  # status reporting must never break processing


# ---------------------------------------------------------------------- #
# main() — consumer-group loop
# ---------------------------------------------------------------------- #

async def _init_consumer_group(redis_client: Redis, stream_key: str) -> None:
    try:
        await redis_client.xgroup_create(stream_key, GROUP_NAME, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def _consume_shard(shard_index: int, processor: WarmProcessor, redis_client: Redis) -> None:
    stream_key = f"telemetry:{shard_index}"
    consumer_name = f"warm-{shard_index}"
    await _init_consumer_group(redis_client, stream_key)

    # Replay this consumer's own pending entries first (crash recovery).
    try:
        pending = await redis_client.xreadgroup(GROUP_NAME, consumer_name, {stream_key: "0-0"}, count=200)
        for _, messages in pending:
            for msg_id, data in messages:
                await processor.handle_message(stream_key, msg_id, data)
    except Exception as e:
        logger.error(f"warm: error replaying pending on {stream_key}: {e}")

    backoff = 1.0
    while True:
        try:
            msgs = await redis_client.xreadgroup(GROUP_NAME, consumer_name, {stream_key: ">"}, count=200, block=1000)
            if not msgs:
                await processor.flush_stale()
                continue
            for _, messages in msgs:
                for msg_id, data in messages:
                    await processor.handle_message(stream_key, msg_id, data)
            backoff = 1.0
        except Exception as e:
            logger.error(f"warm: consumer loop error on {stream_key}: {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


async def main():
    logging.basicConfig(level=settings.log_level)
    logger.info("Starting warm worker...")
    redis_client = Redis.from_url(settings.redis_url)
    await redis_client.ping()

    processor = WarmProcessor(redis_client)
    await processor.recover()

    tasks = [asyncio.create_task(_consume_shard(i, processor, redis_client)) for i in range(N_SHARDS)]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("warm worker shutting down")
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
