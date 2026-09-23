"""Integration tests for GET /machines/{id}/snapshot and /windows."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from backend.app.main import app
from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import WindowAggregate, EtaEstimateRow
from backend.app.services import stream as stream_module

pytestmark = pytest.mark.integration

client = TestClient(app)


@pytest.fixture(autouse=True)
async def _app_redis_client():
    """
    TestClient(app) constructed at module scope (matching this repo's
    existing pattern in tests/test_batch4.py) does NOT run FastAPI's
    lifespan handler unless used as a `with` context manager — so
    main.py's `stream.redis_client = redis.from_url(...)` never executes,
    and any endpoint depending on it (snapshot) would 503. Set it up here
    instead, same effect as what the lifespan does in production.

    This must be a per-test (function-scoped) fixture, not a module-level
    singleton: pytest-asyncio gives each async test its own event loop, and
    a redis.asyncio client's connection binds to whichever loop is running
    when it's first used — reusing one client across tests running in
    different loops raises "Event loop is closed" on the second test.
    """
    stream_module.redis_client = Redis.from_url(settings.redis_url)
    yield
    try:
        # TestClient's synchronous client.get(...) runs the ASGI app through
        # its own internal event loop (separate from pytest-asyncio's loop
        # for this test), so the connection this client actually opened
        # while handling a request is bound to that now-closed loop by the
        # time we get here. The connection itself is already gone either
        # way; this is just cleaning up client-side bookkeeping.
        await stream_module.redis_client.aclose()
    except RuntimeError:
        pass
    stream_module.redis_client = None


def _login(username: str) -> dict:
    resp = client.post("/auth/login", json={"username": username, "password": "demo123"})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def redis_client():
    r = Redis.from_url(settings.redis_url)
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_snapshot_with_no_data_returns_explicit_nulls(redis_client):
    # Deterministic "no data" state, regardless of what a manual verification
    # run against this same dev Redis may have left behind.
    await redis_client.delete("machine_state:EXC001")

    headers = _login("operator")
    resp = client.get("/machines/EXC001/snapshot", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["machine_id"] == "EXC001"
    # Nothing invented: with no Redis state, hot/envelope/context are null.
    assert body["hot"] is None
    assert body["envelope"] is None
    assert body["context"] is None
    assert isinstance(body["recent_events"], list)
    assert isinstance(body["open_incidents"], list)


@pytest.mark.asyncio
async def test_snapshot_reflects_seeded_redis_state(redis_client):
    await redis_client.hset("machine_state:EXC001", mapping={
        "state": "WORKING", "ui_mode": "HUD", "risk_score": "42.0", "risk_level": "ELEVATED",
        "last_seq": "999", "ts": datetime.now(timezone.utc).isoformat(),
    })
    try:
        headers = _login("operator")
        resp = client.get("/machines/EXC001/snapshot", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["hot"]["state"] == "WORKING"
        assert body["hot"]["risk_level"] == "ELEVATED"
        assert body["hot"]["stale"] is False
    finally:
        await redis_client.delete("machine_state:EXC001")


@pytest.mark.asyncio
async def test_snapshot_stale_true_for_old_timestamp(redis_client):
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    await redis_client.hset("machine_state:EXC001", mapping={
        "state": "WORKING", "ui_mode": "HUD", "risk_score": "10.0", "risk_level": "NORMAL",
        "last_seq": "5", "ts": old_ts,
    })
    try:
        headers = _login("operator")
        resp = client.get("/machines/EXC001/snapshot", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["hot"]["stale"] is True
    finally:
        await redis_client.delete("machine_state:EXC001")


def test_snapshot_403_for_supervisor_on_other_site_machine():
    headers = _login("supervisor")  # SITE-A only
    resp = client.get("/machines/EXC003/snapshot", headers=headers)  # EXC003 is SITE-B
    assert resp.status_code == 403


def test_snapshot_404_for_unknown_machine():
    headers = _login("admin")
    resp = client.get("/machines/NOT_A_MACHINE/snapshot", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_windows_endpoint_reflects_db():
    window_id = f"EXC001:{int(datetime.now(timezone.utc).timestamp())}"
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        session.add(WindowAggregate(
            window_id=window_id, machine_id="EXC001", site_id="SITE-A", operator_id="OP-TEST",
            task_id=None, window_start=now, window_end=now + timedelta(seconds=30),
            frame_count=25, idle_attribution={"breakdown_s": {"SITE": 20.0, "OPERATOR": 5.0}},
            anomaly={"method": "SKIPPED", "reason": "NOT_WORKING_WINDOW", "is_anomalous": False},
        ))
        await session.commit()
    try:
        headers = _login("operator")
        resp = client.get("/machines/EXC001/windows?limit=5", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert any(item["window_id"] == window_id for item in body["items"])
        assert body["totals_s"]["SITE"] >= 20.0
    finally:
        async with AsyncSessionLocal() as session:
            from sqlalchemy import delete
            await session.execute(delete(WindowAggregate).where(WindowAggregate.window_id == window_id))
            await session.commit()
