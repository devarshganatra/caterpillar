"""
Event correlator (Stage 3 Batch 3E): consumes events:{shard} on its own
consumer group and groups events into Incidents per ARCH section 6.9. Never
creates incidents from inside the warm worker — this is the ONLY place
incidents get created, extended or escalated.

Run: PYTHONPATH=. python -m backend.app.worker.correlator
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import Event as DBEvent, IncidentRow, IncidentEvent, IncidentTimeline
from backend.app.services.correlator import (
    decide, is_within_correlation_window, IncidentView, EventView,
    timeline_entry_key, merge_into_entry, new_entry_for_event, TimelineEntryView,
)
from contracts.events import Event as EventModel, AlertSeverity, UiPush, UiPushType
from contracts.ids import incident_id as make_incident_id

logger = logging.getLogger(__name__)

GROUP_NAME = "correlator"
N_SHARDS = 4

# UI-only throttle: DB is updated on every EXTEND regardless (see
# _publish_incident_push docstring).
_last_extend_push_wall: dict[str, float] = {}


async def _init_consumer_group(redis_client: Redis, stream_key: str) -> None:
    try:
        await redis_client.xgroup_create(stream_key, GROUP_NAME, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


class Correlator:
    def __init__(self, redis_client: Redis):
        self.redis = redis_client

    async def handle_event(self, event: EventModel) -> str | None:
        """
        Processes one Event under a per-machine advisory lock. Returns the
        UI push action ("opened"|"extended"|"escalated") or None (dedup /
        IGNORE_INFO / no action), for the caller to XADD incidents:work and
        publish on.
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"corr:{event.machine_id}"},
                )

                already_linked = await session.execute(
                    select(IncidentEvent.event_id).where(IncidentEvent.event_id == uuid.UUID(event.event_id))
                )
                if already_linked.scalar_one_or_none() is not None:
                    return None  # SKIP_DUPLICATE

                active_row = await self._active_incident_row(session, event)
                active_view = None
                incident_row: IncidentRow | None = None
                if active_row is not None:
                    active_view = IncidentView(
                        id=str(active_row.id), severity=active_row.severity, status=active_row.status,
                        opened_at=active_row.opened_at, last_event_at=active_row.last_event_at,
                        escalated=active_row.escalated,
                    )
                    incident_row = active_row

                risk_level = await self._risk_level(event.machine_id)
                ev_view = EventView(
                    event_id=event.event_id, type=event.type, severity=event.severity.value,
                    ts=event.ts, machine_id=event.machine_id, evidence=event.evidence,
                )
                decision = decide(ev_view, active_view, risk_level, settings.correlation_window_s)

                if decision.action == "IGNORE_INFO":
                    return None

                if decision.action == "OPEN":
                    incident_row = await self._open_incident(session, event, risk_level)
                    action = "opened"
                elif decision.action == "EXTEND":
                    await self._extend_incident(session, incident_row, event, decision)
                    action = "escalated" if decision.escalate else "extended"
                else:
                    return None

                await self._link_event(session, incident_row.id, event)
                await self._upsert_timeline(session, incident_row.id, ev_view)

            # session.begin() context has committed here.
            return action

    async def _active_incident_row(self, session: AsyncSession, event: EventModel) -> IncidentRow | None:
        result = await session.execute(
            select(IncidentRow).where(
                IncidentRow.machine_id == event.machine_id, IncidentRow.status != "CLOSED",
            ).order_by(IncidentRow.last_event_at.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        if not is_within_correlation_window(event.ts, row.opened_at, row.last_event_at, settings.correlation_window_s):
            return None
        return row

    async def _risk_level(self, machine_id: str) -> str:
        try:
            raw = await self.redis.hget(f"machine_state:{machine_id}", "risk_level")
            return raw.decode() if raw else "NORMAL"
        except Exception:
            return "NORMAL"

    async def _open_incident(self, session: AsyncSession, event: EventModel, risk_level: str) -> IncidentRow:
        iid = uuid.UUID(make_incident_id(event.machine_id, event.event_id))
        task_id = await self._lookup_task_id(session, event.machine_id, event.ts)
        row = IncidentRow(
            id=iid, machine_id=event.machine_id, site_id=event.site_id,
            operator_id=event.operator_id or None, task_id=task_id,
            category=event.type, severity=event.severity.value, escalated=False, status="OPEN",
            opened_at=event.ts, last_event_at=event.ts, trigger_event_id=uuid.UUID(event.event_id),
            event_count=0, risk_level_at_open=risk_level, explanation_status="PENDING",
        )
        stmt = pg_insert(IncidentRow).values(
            id=row.id, machine_id=row.machine_id, site_id=row.site_id, operator_id=row.operator_id,
            task_id=row.task_id, category=row.category, severity=row.severity, escalated=row.escalated,
            status=row.status, opened_at=row.opened_at, last_event_at=row.last_event_at,
            trigger_event_id=row.trigger_event_id, event_count=row.event_count,
            risk_level_at_open=row.risk_level_at_open, explanation_status=row.explanation_status,
        ).on_conflict_do_nothing(index_elements=["id"])
        await session.execute(stmt)

        # Pull in any not-yet-linked INFO/WARNING events for this machine in
        # [ts - window_s, ts) so an incident opened by e.g. a WARNING
        # doesn't lose the INFO context that preceded it (ARCH 6.9).
        lookback = event.ts - timedelta(seconds=settings.correlation_window_s)
        result = await session.execute(
            select(DBEvent).where(
                DBEvent.machine_id == event.machine_id, DBEvent.ts >= lookback, DBEvent.ts < event.ts,
                DBEvent.id != uuid.UUID(event.event_id),
            ).order_by(DBEvent.ts)
        )
        prior_events = result.scalars().all()
        for pe in prior_events:
            already = await session.execute(select(IncidentEvent.event_id).where(IncidentEvent.event_id == pe.id))
            if already.scalar_one_or_none() is not None:
                continue
            pe_view = EventView(event_id=str(pe.id), type=pe.type, severity=pe.severity, ts=pe.ts,
                                 machine_id=pe.machine_id, evidence=pe.evidence)
            await self._link_event_row(session, row.id, pe.id)
            await self._upsert_timeline(session, row.id, pe_view)

        row.event_count = 1 + len(prior_events)
        await session.execute(
            IncidentRow.__table__.update().where(IncidentRow.id == row.id).values(event_count=row.event_count)
        )
        # Refresh from DB (in case ON CONFLICT DO NOTHING meant a concurrent
        # insert won — read back the authoritative row).
        result = await session.execute(select(IncidentRow).where(IncidentRow.id == row.id))
        return result.scalar_one()

    async def _lookup_task_id(self, session: AsyncSession, machine_id: str, ts: datetime) -> str | None:
        from backend.app.db.models import WindowAggregate
        result = await session.execute(
            select(WindowAggregate.task_id).where(
                WindowAggregate.machine_id == machine_id, WindowAggregate.window_start <= ts,
            ).order_by(WindowAggregate.window_start.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def _extend_incident(self, session: AsyncSession, incident: IncidentRow, event: EventModel, decision) -> None:
        old_severity = incident.severity  # capture BEFORE mutating, or the comparison below is meaningless
        incident.last_event_at = max(incident.last_event_at, event.ts)
        incident.severity = decision.new_severity or incident.severity
        # category = "type of the highest-severity entry" (per the schema
        # docstring): update it only when THIS event is what raised the
        # incident's overall severity, not on every extension.
        if SEVERITY_ORDER(incident.severity) > SEVERITY_ORDER(old_severity):
            incident.category = event.type
        incident.event_count = (incident.event_count or 0) + 1

        was_acknowledged = incident.status == "ACKNOWLEDGED"
        if decision.escalate:
            incident.escalated = True
            if was_acknowledged:
                incident.status = "OPEN"

        await session.execute(
            IncidentRow.__table__.update().where(IncidentRow.id == incident.id).values(
                last_event_at=incident.last_event_at, severity=incident.severity, category=incident.category,
                event_count=incident.event_count, escalated=incident.escalated, status=incident.status,
            )
        )
        if decision.escalate:
            await session.execute(pg_insert(IncidentTimeline).values(
                incident_id=incident.id, kind="STATUS", entry_key=f"status:escalated:{event.ts.isoformat()}",
                event_type=None, severity=incident.severity, first_ts=event.ts, last_ts=event.ts, count=1,
                representative_event_id=None, summary=f"Escalated to {incident.severity}"
                + (" (re-opened from ACKNOWLEDGED)" if was_acknowledged else ""),
                actor_id=None,
            ).on_conflict_do_nothing())

    async def _link_event(self, session: AsyncSession, incident_id: uuid.UUID, event: EventModel) -> None:
        await self._link_event_row(session, incident_id, uuid.UUID(event.event_id))

    async def _link_event_row(self, session: AsyncSession, incident_id: uuid.UUID, event_id: uuid.UUID) -> None:
        stmt = pg_insert(IncidentEvent).values(
            incident_id=incident_id, event_id=event_id,
        ).on_conflict_do_nothing()
        await session.execute(stmt)

    async def _upsert_timeline(self, session: AsyncSession, incident_id: uuid.UUID, ev_view: EventView) -> None:
        key = timeline_entry_key(ev_view)
        result = await session.execute(
            select(IncidentTimeline).where(
                IncidentTimeline.incident_id == incident_id, IncidentTimeline.entry_key == key,
                IncidentTimeline.kind == "EVENT",
            ).order_by(IncidentTimeline.last_ts.desc()).limit(1)
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            existing_view = TimelineEntryView(
                entry_key=existing.entry_key, event_type=existing.event_type, severity=existing.severity,
                first_ts=existing.first_ts, last_ts=existing.last_ts, count=existing.count,
                representative_event_id=str(existing.representative_event_id), summary=existing.summary,
            )
            merged = merge_into_entry(existing_view, ev_view)
            if merged is not None:
                await session.execute(
                    IncidentTimeline.__table__.update().where(IncidentTimeline.id == existing.id).values(
                        last_ts=merged.last_ts, count=merged.count,
                    )
                )
                return

        new_entry = new_entry_for_event(ev_view)
        stmt = pg_insert(IncidentTimeline).values(
            incident_id=incident_id, kind="EVENT", entry_key=new_entry.entry_key,
            event_type=new_entry.event_type, severity=new_entry.severity,
            first_ts=new_entry.first_ts, last_ts=new_entry.last_ts, count=new_entry.count,
            representative_event_id=uuid.UUID(new_entry.representative_event_id), summary=new_entry.summary,
        ).on_conflict_do_nothing(constraint="uq_incident_timeline_key_first_ts")
        await session.execute(stmt)

    async def publish(self, event: EventModel, incident_id: uuid.UUID, action: str) -> None:
        if action == "extended":
            key = str(incident_id)
            now = time.monotonic()
            last = _last_extend_push_wall.get(key, 0.0)
            if now - last < settings.correlator_extend_push_throttle_s:
                return  # DB already updated; only the UI push is throttled
            _last_extend_push_wall[key] = now

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(IncidentRow).where(IncidentRow.id == incident_id))
            row = result.scalar_one_or_none()
        if row is None:
            return

        payload = {
            "action": action,
            "incident": {
                "id": str(row.id), "machine_id": row.machine_id, "site_id": row.site_id,
                "operator_id": row.operator_id, "task_id": row.task_id, "category": row.category,
                "severity": row.severity, "escalated": row.escalated, "status": row.status,
                "opened_at": row.opened_at.isoformat(), "last_event_at": row.last_event_at.isoformat(),
                "event_count": row.event_count, "explanation_status": row.explanation_status,
            },
        }
        push = UiPush(type=UiPushType.incident, machine_id=row.machine_id, ts=event.ts, payload=payload)
        await self.redis.publish(f"ui:{row.machine_id}", push.model_dump_json())

        if action in ("opened", "escalated"):
            try:
                await self.redis.xadd("incidents:work", {"incident_id": str(row.id), "reason": action})
            except Exception as e:
                logger.error(f"correlator: failed to XADD incidents:work for {row.id}: {e}")


def SEVERITY_ORDER(sev: str | None) -> int:
    from backend.app.services.correlator import SEVERITY_RANK
    return SEVERITY_RANK.get(sev, 0)


# ---------------------------------------------------------------------- #
# main() — consumer-group loop + startup reconcile
# ---------------------------------------------------------------------- #

async def _reconcile(correlator: Correlator) -> None:
    """
    Covers the tiny crash window between a producer's DB commit and its
    XADD to events:{shard}: on startup, find WARNING+/CRITICAL events from
    the last correlator_reconcile_lookback_s that aren't linked to any
    incident yet, and run them through the same handler.
    """
    lookback = datetime.now(timezone.utc) - timedelta(seconds=settings.correlator_reconcile_lookback_s)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DBEvent).where(
                DBEvent.ts >= lookback, DBEvent.severity.in_(["WARNING", "CRITICAL"]),
                ~DBEvent.id.in_(select(IncidentEvent.event_id)),
            ).order_by(DBEvent.ts, DBEvent.id)
        )
        missed = result.scalars().all()

    for row in missed:
        event = EventModel(
            event_id=str(row.id), type=row.type, severity=AlertSeverity(row.severity),
            machine_id=row.machine_id, operator_id=row.operator_id, site_id=row.site_id,
            ts=row.ts, source_engine=row.source_engine, evidence=row.evidence,
        )
        action = await correlator.handle_event(event)
        if action:
            logger.info(f"correlator reconcile: {action} for event {event.event_id}")
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(IncidentEvent.incident_id).where(IncidentEvent.event_id == row.id))
                iid = result.scalar_one_or_none()
            if iid:
                await correlator.publish(event, iid, action)
    if missed:
        logger.info(f"correlator reconcile: processed {len(missed)} previously-unlinked events")


async def _consume_shard(shard_index: int, correlator: Correlator, redis_client: Redis) -> None:
    stream_key = f"events:{shard_index}"
    consumer_name = f"corr-{shard_index}"
    await _init_consumer_group(redis_client, stream_key)

    try:
        pending = await redis_client.xreadgroup(GROUP_NAME, consumer_name, {stream_key: "0-0"}, count=200)
        for _, messages in pending:
            for msg_id, data in messages:
                await _process_message(correlator, redis_client, stream_key, msg_id, data)
    except Exception as e:
        logger.error(f"correlator: error replaying pending on {stream_key}: {e}")

    backoff = 1.0
    while True:
        try:
            msgs = await redis_client.xreadgroup(GROUP_NAME, consumer_name, {stream_key: ">"}, count=100, block=1000)
            if not msgs:
                continue
            for _, messages in msgs:
                for msg_id, data in messages:
                    await _process_message(correlator, redis_client, stream_key, msg_id, data)
            backoff = 1.0
        except Exception as e:
            logger.error(f"correlator: consumer loop error on {stream_key}: {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


async def _process_message(correlator: Correlator, redis_client: Redis, stream_key: str, msg_id: bytes, data: dict) -> None:
    try:
        raw = data.get(b"data")
        if raw is None:
            await redis_client.xack(stream_key, GROUP_NAME, msg_id)
            return
        event = EventModel.model_validate_json(raw)
    except Exception as e:
        logger.error(f"correlator: failed to parse event message: {e}")
        await redis_client.xack(stream_key, GROUP_NAME, msg_id)
        return

    try:
        action = await correlator.handle_event(event)
    except Exception as e:
        logger.error(f"correlator: handle_event failed for {event.event_id}, leaving pending: {e}")
        return  # no XACK: retried on next read/restart

    if action:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(IncidentEvent.incident_id).where(IncidentEvent.event_id == uuid.UUID(event.event_id)))
            iid = result.scalar_one_or_none()
        if iid:
            await correlator.publish(event, iid, action)
        logger.info(f"correlator: {action} incident_id={iid} event_id={event.event_id} machine={event.machine_id}")

    await redis_client.xack(stream_key, GROUP_NAME, msg_id)


async def main():
    logging.basicConfig(level=settings.log_level)
    logger.info("Starting correlator worker...")
    redis_client = Redis.from_url(settings.redis_url)
    await redis_client.ping()

    correlator = Correlator(redis_client)
    await _reconcile(correlator)

    tasks = [asyncio.create_task(_consume_shard(i, correlator, redis_client)) for i in range(N_SHARDS)]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("correlator worker shutting down")
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
