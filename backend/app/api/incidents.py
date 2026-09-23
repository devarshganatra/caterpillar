"""
RBAC-protected incident APIs (Stage 3 Batch 3F). Mutations (ack/close) are
audited via the existing hash-chained audit log — the same infrastructure
used for every other audited action in this codebase.
"""
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
from backend.app.db.models import (
    IncidentRow, IncidentTimeline, IncidentExplanationRow, Event as DBEvent, User, UserSiteAccess,
)
from backend.app.api.deps import get_current_user, require_roles, verify_site_access
from backend.app.services.audit_chain import append_audit_log

router = APIRouter()


def _incident_summary(row: IncidentRow) -> dict:
    return {
        "id": str(row.id), "machine_id": row.machine_id, "site_id": row.site_id,
        "operator_id": row.operator_id, "task_id": row.task_id, "category": row.category,
        "severity": row.severity, "escalated": row.escalated, "status": row.status,
        "opened_at": row.opened_at, "last_event_at": row.last_event_at,
        "event_count": row.event_count, "explanation_status": row.explanation_status,
    }


async def _get_incident_or_404(incident_id: str, db: AsyncSession) -> IncidentRow:
    try:
        iid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    result = await db.execute(select(IncidentRow).where(IncidentRow.id == iid))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return row


async def _authorize_incident_read(row: IncidentRow, user: User, db: AsyncSession) -> None:
    if user.role.value == "OPERATOR":
        if row.operator_id != str(user.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your incident")
        return
    has_access = await verify_site_access(row.site_id, user, db)
    if not has_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this site")


@router.get("")
async def list_incidents(
    site_id: Optional[str] = None, machine_id: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"), since: Optional[datetime] = None,
    limit: int = Query(50, le=200), offset: int = 0,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    stmt = select(IncidentRow)

    if user.role.value == "OPERATOR":
        stmt = stmt.where(IncidentRow.operator_id == str(user.id))
    elif user.role.value == "SUPERVISOR":
        access_result = await db.execute(
            select(UserSiteAccess.site_id).where(UserSiteAccess.user_id == user.id)
        )
        allowed_sites = set(access_result.scalars().all())
        if site_id is not None:
            if site_id not in allowed_sites:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this site")
            stmt = stmt.where(IncidentRow.site_id == site_id)
        else:
            if not allowed_sites:
                return {"items": [], "total": 0}
            stmt = stmt.where(IncidentRow.site_id.in_(allowed_sites))
    else:  # ADMIN
        if site_id is not None:
            stmt = stmt.where(IncidentRow.site_id == site_id)

    if machine_id is not None:
        stmt = stmt.where(IncidentRow.machine_id == machine_id)
    if status_filter is not None:
        stmt = stmt.where(IncidentRow.status == status_filter)
    if since is not None:
        stmt = stmt.where(IncidentRow.opened_at >= since)

    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar_one()

    stmt = stmt.order_by(IncidentRow.opened_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return {"items": [_incident_summary(r) for r in rows], "total": total}


@router.get("/{incident_id}")
async def get_incident(
    incident_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    row = await _get_incident_or_404(incident_id, db)
    await _authorize_incident_read(row, user, db)

    explanation = None
    exp_result = await db.execute(
        select(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == row.id)
        .order_by(IncidentExplanationRow.created_at.desc()).limit(1)
    )
    exp_row = exp_result.scalar_one_or_none()
    if exp_row is not None:
        explanation = {
            "source": exp_row.source, "model_name": exp_row.model_name, "summary": exp_row.summary,
            "probable_causes": exp_row.probable_causes, "recommended_actions": exp_row.recommended_actions,
            "lesson": exp_row.lesson, "training_refs": exp_row.training_refs, "confidence": exp_row.confidence,
            "fallback_reason": exp_row.fallback_reason, "created_at": exp_row.created_at,
        }

    summary = _incident_summary(row)
    summary["explanation"] = explanation
    return summary


@router.get("/{incident_id}/timeline")
async def get_incident_timeline(
    incident_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    row = await _get_incident_or_404(incident_id, db)
    await _authorize_incident_read(row, user, db)

    result = await db.execute(
        select(IncidentTimeline).where(IncidentTimeline.incident_id == row.id)
        .order_by(IncidentTimeline.first_ts, IncidentTimeline.id)
    )
    entries = result.scalars().all()

    representative_ids = [e.representative_event_id for e in entries if e.representative_event_id is not None]
    events_by_id = {}
    if representative_ids:
        ev_result = await db.execute(select(DBEvent).where(DBEvent.id.in_(representative_ids)))
        events_by_id = {e.id: e for e in ev_result.scalars().all()}

    items = []
    for e in entries:
        rep_event = events_by_id.get(e.representative_event_id)
        items.append({
            "entry_key": e.entry_key, "kind": e.kind, "event_type": e.event_type, "severity": e.severity,
            "first_ts": e.first_ts, "last_ts": e.last_ts, "count": e.count, "summary": e.summary,
            "actor_id": e.actor_id,
            "representative_event": {
                "id": str(rep_event.id), "type": rep_event.type, "severity": rep_event.severity,
                "ts": rep_event.ts, "evidence": rep_event.evidence,
            } if rep_event is not None else None,
        })
    return items


class CloseRequest(BaseModel):
    note: str = Field(min_length=1, max_length=500)


@router.post("/{incident_id}/ack")
async def ack_incident(
    incident_id: str, user: User = Depends(require_roles(["SUPERVISOR", "ADMIN"])),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_incident_or_404(incident_id, db)
    await _authorize_incident_read(row, user, db)

    if row.status != "OPEN":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                             detail=f"Cannot acknowledge incident in status {row.status}")

    now = datetime.now(timezone.utc)
    from_status = row.status
    await db.execute(
        IncidentRow.__table__.update().where(IncidentRow.id == row.id).values(
            status="ACKNOWLEDGED", acknowledged_at=now, acknowledged_by=user.id,
        )
    )
    await db.execute(pg_insert(IncidentTimeline).values(
        incident_id=row.id, kind="STATUS", entry_key=f"status:acknowledged:{now.isoformat()}",
        event_type=None, severity=row.severity, first_ts=now, last_ts=now, count=1,
        representative_event_id=None, summary="Acknowledged by supervisor", actor_id=str(user.id),
    ).on_conflict_do_nothing())
    await append_audit_log(
        db, actor_id=str(user.id), action="INCIDENT_ACK", target_type="incident", target_id=incident_id,
        payload={"from": from_status, "to": "ACKNOWLEDGED"},
    )
    await db.commit()

    await _publish_incident_push(row.machine_id, "acknowledged", incident_id, db)
    return {"status": "ACKNOWLEDGED"}


@router.post("/{incident_id}/close")
async def close_incident(
    incident_id: str, body: CloseRequest, user: User = Depends(require_roles(["SUPERVISOR", "ADMIN"])),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_incident_or_404(incident_id, db)
    await _authorize_incident_read(row, user, db)

    if row.status not in ("OPEN", "ACKNOWLEDGED"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                             detail=f"Cannot close incident in status {row.status}")

    now = datetime.now(timezone.utc)
    from_status = row.status
    note_hash = hashlib.sha256(body.note.encode("utf-8")).hexdigest()

    await db.execute(
        IncidentRow.__table__.update().where(IncidentRow.id == row.id).values(
            status="CLOSED", closed_at=now, closed_by=user.id, close_note=body.note,
        )
    )
    await db.execute(pg_insert(IncidentTimeline).values(
        incident_id=row.id, kind="STATUS", entry_key=f"status:closed:{now.isoformat()}",
        event_type=None, severity=row.severity, first_ts=now, last_ts=now, count=1,
        representative_event_id=None, summary=f"Closed: {body.note}", actor_id=str(user.id),
    ).on_conflict_do_nothing())
    # The note text lives on the row itself; only its hash goes in the
    # tamper-evident audit payload, so the audit chain doesn't duplicate
    # (and can't leak, if the note were ever sensitive) the full text.
    await append_audit_log(
        db, actor_id=str(user.id), action="INCIDENT_CLOSE", target_type="incident", target_id=incident_id,
        payload={"from": from_status, "note_sha256": note_hash},
    )
    await db.commit()

    await _publish_incident_push(row.machine_id, "closed", incident_id, db)
    return {"status": "CLOSED"}


@router.post("/{incident_id}/explain", status_code=status.HTTP_202_ACCEPTED)
async def request_incident_explanation(
    incident_id: str, user: User = Depends(require_roles(["SUPERVISOR", "ADMIN"])),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually (re-)queues an incident for the cold worker — e.g. after a
    fallback explanation, to retry once Groq is back up. Does not block on
    the actual generation; the cold worker picks it up asynchronously.
    """
    row = await _get_incident_or_404(incident_id, db)
    await _authorize_incident_read(row, user, db)

    await append_audit_log(
        db, actor_id=str(user.id), action="INCIDENT_EXPLAIN_REQUEST", target_type="incident",
        target_id=incident_id, payload={},
    )
    await db.commit()

    from backend.app.services import stream as stream_module
    if stream_module.redis_client is not None:
        try:
            await stream_module.redis_client.xadd("incidents:work", {"incident_id": incident_id, "reason": "manual"})
        except Exception:
            pass  # the audit row and request are still recorded even if the queue is unreachable

    return {"status": "QUEUED"}


async def _publish_incident_push(machine_id: str, action: str, incident_id: str, db: AsyncSession) -> None:
    """Best-effort UI push after a mutation — never fails the request if Redis is unreachable."""
    from backend.app.services import stream as stream_module
    from contracts.events import UiPush, UiPushType
    from datetime import datetime as _dt, timezone as _tz

    if stream_module.redis_client is None:
        return
    result = await db.execute(select(IncidentRow).where(IncidentRow.id == uuid.UUID(incident_id)))
    row = result.scalar_one_or_none()
    if row is None:
        return
    try:
        push = UiPush(
            type=UiPushType.incident, machine_id=machine_id, ts=_dt.now(_tz.utc),
            payload={"action": action, "incident": _incident_summary(row)},
        )
        # Pydantic BaseModel with datetime fields needs mode="json" via model_dump_json,
        # which handles the nested datetimes in the summary dict automatically.
        await stream_module.redis_client.publish(f"ui:{machine_id}", push.model_dump_json())
    except Exception:
        pass
