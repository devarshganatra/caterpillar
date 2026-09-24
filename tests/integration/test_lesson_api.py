"""
Integration tests for backend.app.api.lessons — RBAC and read-marking,
against the real seeded demo users (matches tests/integration/test_incident_api.py's pattern).
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from backend.app.main import app
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import LessonRow, IncidentRow, Event as DBEvent

pytestmark = pytest.mark.integration

client = TestClient(app)

OPERATOR_ID = "11111111-1111-1111-1111-111111111111"  # seed_db.py's real operator
OTHER_OPERATOR_ID = "not-the-real-operator"


def _login(username: str) -> dict:
    resp = client.post("/auth/login", json={"username": username, "password": "demo123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _seed_lesson(operator_id: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    now = datetime.now(timezone.utc)
    incident_id = uuid.uuid4()
    event_id = uuid.uuid4()
    lesson_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(DBEvent(
            id=event_id, type="SEATBELT_VIOLATION", severity="CRITICAL", machine_id="EXC001",
            operator_id=operator_id, site_id="SITE-A", ts=now, source_engine="test@1.0",
            evidence={"state": "WORKING"}, frame_seq=int(now.timestamp()),
        ))
        session.add(IncidentRow(
            id=incident_id, machine_id="EXC001", site_id="SITE-A", operator_id=operator_id, task_id=None,
            category="SEATBELT_VIOLATION", severity="CRITICAL", escalated=False, status="OPEN",
            opened_at=now, last_event_at=now, trigger_event_id=event_id, event_count=1,
            risk_level_at_open="NORMAL", explanation_status="PENDING",
        ))
        session.add(LessonRow(
            id=lesson_id, incident_id=incident_id, machine_id="EXC001", operator_id=operator_id,
            title="Test Lesson", short_tip="Do the safe thing.", explanation="Because it is safer.",
            knowledge_refs=["seatbelt-safety#overview"], source="FALLBACK", status="FALLBACK",
            fallback_reason="NO_API_KEY",
        ))
        await session.commit()
    return lesson_id, incident_id, event_id


async def _cleanup(incident_id: uuid.UUID, event_id: uuid.UUID):
    async with AsyncSessionLocal() as session:
        await session.execute(delete(LessonRow).where(LessonRow.incident_id == incident_id))
        await session.execute(delete(IncidentRow).where(IncidentRow.id == incident_id))
        await session.execute(delete(DBEvent).where(DBEvent.id == event_id))
        await session.commit()


@pytest.mark.asyncio
async def test_operator_can_list_and_read_own_lesson():
    lesson_id, incident_id, event_id = await _seed_lesson(OPERATOR_ID)
    try:
        headers = _login("operator")

        list_resp = client.get("/lessons", headers=headers)
        assert list_resp.status_code == 200
        ids = {item["id"] for item in list_resp.json()["items"]}
        assert str(lesson_id) in ids

        get_resp = client.get(f"/lessons/{lesson_id}", headers=headers)
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["title"] == "Test Lesson"
        assert body["explanation"] == "Because it is safer."
        assert body["read_at"] is not None  # marked read on this first fetch

        first_read_at = body["read_at"]
        get_resp2 = client.get(f"/lessons/{lesson_id}", headers=headers)
        assert get_resp2.status_code == 200
        assert get_resp2.json()["read_at"] == first_read_at  # not re-stamped on a later fetch
    finally:
        await _cleanup(incident_id, event_id)


@pytest.mark.asyncio
async def test_operator_cannot_read_another_operators_lesson():
    lesson_id, incident_id, event_id = await _seed_lesson(OTHER_OPERATOR_ID)
    try:
        headers = _login("operator")
        resp = client.get(f"/lessons/{lesson_id}", headers=headers)
        assert resp.status_code == 403
    finally:
        await _cleanup(incident_id, event_id)


@pytest.mark.asyncio
async def test_supervisor_can_read_any_lesson():
    lesson_id, incident_id, event_id = await _seed_lesson(OPERATOR_ID)
    try:
        headers = _login("supervisor")
        resp = client.get(f"/lessons/{lesson_id}", headers=headers)
        assert resp.status_code == 200
    finally:
        await _cleanup(incident_id, event_id)


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected():
    resp = client.get("/lessons")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_unknown_lesson_id_is_404():
    headers = _login("operator")
    resp = client.get(f"/lessons/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404
