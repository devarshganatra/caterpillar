"""
Deterministic Incident Packet builder (Stage 3 Batch 3G). This is the ONLY
data the LLM ever sees — no raw DB access, no arbitrary queries. Bounded in
size (packet_max_timeline / packet_max_events), and every reference the LLM
is allowed to cite is enumerated explicitly in allowed_event_ids /
allowed_chunk_ids for the grounding validator (Batch 3H) to check against.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.db.models import (
    IncidentRow, IncidentTimeline, Event as DBEvent, WindowAggregate, EtaEstimateRow, Task,
)
from backend.app.knowledge.retriever import KnowledgeRetriever, query_for_incident
from backend.app.services.stream import parse_flat_context
from contracts.machine_config import MACHINES

# Evidence fields allowed into the packet per event type — keeps the packet
# bounded and avoids leaking anything not relevant to an explanation.
EVIDENCE_WHITELIST = {
    "SEATBELT_VIOLATION": {"state"},
    "PROXIMITY_BREACH": {"zone", "distance_m", "effective_radius_m", "multiplier"},
    "OVERSPEED_CONDITION": {"speed_kmh", "cap_kmh", "multiplier"},
    "HEALTH_THRESHOLD": {"metric", "value", "threshold"},
    "OPERATIONAL_ANOMALY": {"score", "threshold", "method", "drivers"},
    "IDLE_DEVIATION": {"operator_idle_ratio", "expected_idle_ratio", "deviation_ratio", "breakdown_s"},
    "ETA_SLIP": {"baseline_p50_min", "eta_total_p50_min", "slip_pct", "band"},
}


def _whitelisted_evidence(event_type: str, evidence: dict) -> dict:
    allowed = EVIDENCE_WHITELIST.get(event_type)
    if allowed is None:
        return {}  # unknown type: no evidence leaks through by default
    return {k: v for k, v in evidence.items() if k in allowed}


async def build_incident_packet(
    session: AsyncSession, redis: Redis, incident_id, retriever: KnowledgeRetriever,
) -> dict:
    incident = (await session.execute(select(IncidentRow).where(IncidentRow.id == incident_id))).scalar_one()

    # --- timeline (bounded) ---
    timeline_result = await session.execute(
        select(IncidentTimeline).where(IncidentTimeline.incident_id == incident.id)
        .order_by(IncidentTimeline.first_ts, IncidentTimeline.id)
    )
    all_entries = timeline_result.scalars().all()
    truncated = len(all_entries) > settings.packet_max_timeline
    if truncated:
        # Keep the first N (how it started) plus the most recent rest (how
        # it's going). n_head is capped at packet_max_timeline itself so
        # this never returns MORE than the configured max — a plain
        # `packet_max_timeline - 10` goes negative if the max is configured
        # below 10, which previously made the tail slice wrap around and
        # return duplicate entries (more rows than the cap, not fewer).
        n_head = min(10, settings.packet_max_timeline)
        n_tail = settings.packet_max_timeline - n_head
        entries = list(all_entries[:n_head])
        if n_tail > 0:
            entries += list(all_entries[-n_tail:])
    else:
        entries = list(all_entries)

    timeline = [
        {
            "entry_key": e.entry_key, "kind": e.kind, "event_type": e.event_type, "severity": e.severity,
            "first_ts": e.first_ts.isoformat(), "last_ts": e.last_ts.isoformat(), "count": e.count,
            "summary": e.summary, "representative_event_id": str(e.representative_event_id) if e.representative_event_id else None,
        }
        for e in entries
    ]

    # --- representative events (bounded, evidence whitelisted) ---
    rep_ids = [e.representative_event_id for e in entries if e.representative_event_id is not None]
    rep_ids = rep_ids[: settings.packet_max_events]
    events = []
    allowed_event_ids: set[str] = set()
    if rep_ids:
        ev_result = await session.execute(select(DBEvent).where(DBEvent.id.in_(rep_ids)))
        for e in ev_result.scalars().all():
            events.append({
                "event_id": str(e.id), "type": e.type, "severity": e.severity,
                "ts": e.ts.isoformat(), "source_engine": e.source_engine,
                "evidence": _whitelisted_evidence(e.type, e.evidence or {}),
            })
            allowed_event_ids.add(str(e.id))

    # --- machine ---
    machine_cfg = MACHINES.get(incident.machine_id)
    raw_state = await redis.hgetall(f"machine_state:{incident.machine_id}")

    def _hget(key: bytes) -> str | None:
        val = raw_state.get(key) if raw_state else None
        return val.decode() if val else None

    machine = {
        "machine_id": incident.machine_id,
        "type": machine_cfg.type if machine_cfg else None,
        "model": machine_cfg.model if machine_cfg else None,
        "age_years": machine_cfg.age_years if machine_cfg else None,
        "state": _hget(b"state"),
        "risk_level": _hget(b"risk_level"),
    }

    # --- task + ETA ---
    task = {"task_id": incident.task_id, "task_type": None, "target_cycles": None, "eta": None}
    if incident.task_id:
        task_row = (await session.execute(select(Task).where(Task.id == incident.task_id))).scalar_one_or_none()
        if task_row is not None:
            task["task_type"] = task_row.task_type
            task["target_cycles"] = task_row.target_cycles

        eta_result = await session.execute(
            select(EtaEstimateRow).where(EtaEstimateRow.task_id == incident.task_id, EtaEstimateRow.kind == "LIVE")
            .order_by(EtaEstimateRow.ts.desc()).limit(1)
        )
        eta_row = eta_result.scalar_one_or_none()
        if eta_row is not None:
            payload = dict(eta_row.payload)
            payload["factors"] = payload.get("factors", [])[:5]  # top 5 only
            task["eta"] = payload

    # --- context (latest window's context, or current site context) ---
    context = None
    window_result = await session.execute(
        select(WindowAggregate).where(WindowAggregate.machine_id == incident.machine_id)
        .order_by(WindowAggregate.window_start.desc()).limit(1)
    )
    latest_window_row = window_result.scalar_one_or_none()
    attribution = None
    if latest_window_row is not None:
        context = latest_window_row.context
        if latest_window_row.idle_attribution:
            ia = latest_window_row.idle_attribution
            attribution = {
                "breakdown_s": ia.get("breakdown_s"), "primary_cause": ia.get("primary_cause"),
                "operator_deviation_flag": ia.get("operator_deviation_flag"),
            }
    if context is None:
        raw_ctx = await redis.hgetall(f"context:{incident.site_id}")
        ctx_frame = parse_flat_context(raw_ctx)
        context = ctx_frame.model_dump(mode="json") if ctx_frame else None

    # --- history (aggregate only, no other operators' data) ---
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    history_result = await session.execute(
        select(IncidentRow.category, func.count()).where(
            IncidentRow.machine_id == incident.machine_id, IncidentRow.opened_at >= week_ago,
        ).group_by(IncidentRow.category)
    )
    history = {category: count for category, count in history_result.all()}

    # --- knowledge ---
    partial_packet_for_query = {"timeline": timeline, "context": context}
    query, tags = query_for_incident(partial_packet_for_query)
    scored = retriever.search(query, tags, k=settings.knowledge_top_k)
    knowledge = [
        {"chunk_id": sc.chunk.chunk_id, "title": sc.chunk.title, "heading": sc.chunk.heading,
         "text": sc.chunk.text[:800]}
        for sc in scored
    ]
    allowed_chunk_ids = {k["chunk_id"] for k in knowledge}

    packet = {
        "incident": {
            "id": str(incident.id), "machine_id": incident.machine_id, "site_id": incident.site_id,
            "severity": incident.severity, "escalated": incident.escalated, "status": incident.status,
            "opened_at": incident.opened_at.isoformat(), "last_event_at": incident.last_event_at.isoformat(),
            "category": incident.category,
        },
        "timeline": timeline,
        "timeline_truncated": truncated,
        "events": events,
        "machine": machine,
        "task": task,
        "context": context,
        "attribution": attribution,
        "history": history,
        "knowledge": knowledge,
        "allowed_event_ids": sorted(allowed_event_ids),
        "allowed_chunk_ids": sorted(allowed_chunk_ids),
        # operator_id is included as an opaque identifier only — no names,
        # no other personal data.
        "operator_id": incident.operator_id,
    }
    return packet


def packet_hash(packet: dict) -> str:
    canonical = json.dumps(packet, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
