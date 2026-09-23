"""
Integration tests for backend.app.api.incidents — RBAC, ack/close state
machine, audit trail. Uses the existing seeded demo users (operator/
supervisor/admin) and seeds incident rows directly via the DB, matching the
pattern already used in tests/test_batch4.py.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend.app.main import app
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import IncidentRow, IncidentTimeline, Event as DBEvent, AuditLog

pytestmark = pytest.mark.integration

client = TestClient(app)

OPERATOR_ID = "11111111-1111-1111-1111-111111111111"


def _login(username: str) -> dict:
    resp = client.post("/auth/login", json={"username": username, "password": "demo123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _seed_incident(machine_id: str, site_id: str, operator_id: str | None,
                          status_: str = "OPEN", severity: str = "WARNING") -> str:
    event_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        session.add(DBEvent(
            id=event_id, type="SEATBELT_VIOLATION", severity=severity, machine_id=machine_id,
            operator_id=operator_id or "UNASSIGNED", site_id=site_id, ts=now,
            source_engine="test@1.0", evidence={"state": "WORKING"}, frame_seq=int(now.timestamp()),
        ))
        session.add(IncidentRow(
            id=incident_id, machine_id=machine_id, site_id=site_id, operator_id=operator_id,
            task_id=None, category="SEATBELT_VIOLATION", severity=severity, escalated=False,
            status=status_, opened_at=now, last_event_at=now, trigger_event_id=event_id,
            event_count=1, risk_level_at_open="NORMAL", explanation_status="PENDING",
        ))
        session.add(IncidentTimeline(
            incident_id=incident_id, kind="EVENT", entry_key="SEATBELT_VIOLATION:",
            event_type="SEATBELT_VIOLATION", severity=severity, first_ts=now, last_ts=now,
            count=1, representative_event_id=event_id, summary="Seatbelt unfastened while WORKING",
        ))
        await session.commit()
    return str(incident_id)


async def _cleanup(incident_ids: list[str]):
    async with AsyncSessionLocal() as session:
        for iid in incident_ids:
            row = (await session.execute(select(IncidentRow).where(IncidentRow.id == uuid.UUID(iid)))).scalar_one_or_none()
            if row is None:
                continue
            trigger_event_id = row.trigger_event_id
            await session.execute(delete(IncidentTimeline).where(IncidentTimeline.incident_id == row.id))
            await session.execute(delete(IncidentRow).where(IncidentRow.id == row.id))
            await session.execute(delete(DBEvent).where(DBEvent.id == trigger_event_id))
        await session.commit()


@pytest.fixture
def site_a_incident():
    import asyncio
    iid = asyncio.run(_seed_incident("EXC001", "SITE-A", OPERATOR_ID))
    yield iid
    asyncio.run(_cleanup([iid]))


@pytest.fixture
def site_b_incident():
    import asyncio
    iid = asyncio.run(_seed_incident("EXC003", "SITE-B", None))
    yield iid
    asyncio.run(_cleanup([iid]))


# --- RBAC matrix -----------------------------------------------------------

def test_unauthenticated_401():
    resp = client.get("/incidents/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401


def test_operator_can_read_own_incident(site_a_incident):
    headers = _login("operator")
    resp = client.get(f"/incidents/{site_a_incident}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["operator_id"] == OPERATOR_ID


def test_operator_403_on_other_operators_incident(site_b_incident):
    """site_b_incident has operator_id=None (UNASSIGNED machine), so it's never 'their' incident."""
    headers = _login("operator")
    resp = client.get(f"/incidents/{site_b_incident}", headers=headers)
    assert resp.status_code == 403


def test_supervisor_200_on_own_site(site_a_incident):
    headers = _login("supervisor")
    resp = client.get(f"/incidents/{site_a_incident}", headers=headers)
    assert resp.status_code == 200


def test_supervisor_403_on_other_site(site_b_incident):
    headers = _login("supervisor")  # supervisor only has SITE-A access
    resp = client.get(f"/incidents/{site_b_incident}", headers=headers)
    assert resp.status_code == 403


def test_admin_200_on_any_site(site_a_incident, site_b_incident):
    headers = _login("admin")
    for iid in (site_a_incident, site_b_incident):
        resp = client.get(f"/incidents/{iid}", headers=headers)
        assert resp.status_code == 200


def test_404_for_missing_incident():
    headers = _login("admin")
    resp = client.get("/incidents/00000000-0000-0000-0000-000000000000", headers=headers)
    assert resp.status_code == 404


# --- ack/close state machine + audit ---------------------------------------

def test_ack_then_close_with_audit_trail(site_a_incident):
    # tests/test_batch4.py::test_audit_tamper_evidence deliberately corrupts
    # the audit chain and never restores it (by design — it's testing tamper
    # detection). Truncate first, same convention that test itself uses, so
    # this test's "GET /audit/verify -> valid: true" assertion is meaningful
    # regardless of what ran before it in the shared dev DB.
    import asyncio
    from sqlalchemy import text as _text

    async def _reset_audit_log():
        async with AsyncSessionLocal() as session:
            await session.execute(_text("TRUNCATE TABLE audit_log RESTART IDENTITY"))
            await session.commit()
    asyncio.run(_reset_audit_log())

    headers = _login("supervisor")

    resp = client.post(f"/incidents/{site_a_incident}/ack", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACKNOWLEDGED"

    # double-ack -> 409
    resp = client.post(f"/incidents/{site_a_incident}/ack", headers=headers)
    assert resp.status_code == 409

    resp = client.post(f"/incidents/{site_a_incident}/close", headers=headers, json={"note": "Resolved on site."})
    assert resp.status_code == 200
    assert resp.json()["status"] == "CLOSED"

    # close-after-close -> 409
    resp = client.post(f"/incidents/{site_a_incident}/close", headers=headers, json={"note": "again"})
    assert resp.status_code == 409

    # audit_log has both actions with the correct action/target
    async def _check_audit_rows():
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(AuditLog).order_by(AuditLog.id))
            return result.scalars().all()
    import asyncio
    rows = asyncio.run(_check_audit_rows())
    actions = [(r.action, r.target_type, r.target_id) for r in rows]
    assert ("INCIDENT_ACK", "incident", site_a_incident) in actions
    assert ("INCIDENT_CLOSE", "incident", site_a_incident) in actions

    # ...and the chain still verifies
    audit_headers = _login("admin")
    resp = client.get("/audit/verify", headers=audit_headers)
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_operator_cannot_ack(site_a_incident):
    headers = _login("operator")
    resp = client.post(f"/incidents/{site_a_incident}/ack", headers=headers)
    assert resp.status_code == 403


def test_timeline_ordering(site_a_incident):
    headers = _login("admin")
    resp = client.get(f"/incidents/{site_a_incident}/timeline", headers=headers)
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) >= 1
    assert entries[0]["representative_event"] is not None
    assert entries[0]["representative_event"]["type"] == "SEATBELT_VIOLATION"
