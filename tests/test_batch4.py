import pytest
import uuid
from fastapi.testclient import TestClient
from datetime import datetime, timezone
import asyncio

from backend.app.main import app
from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.services.audit_chain import append_audit_log, verify_chain
from backend.app.db.models import AuditLog
from sqlalchemy import text

pytestmark = pytest.mark.integration

client = TestClient(app)

def test_auth_failures():
    # Invalid credentials
    resp = client.post("/auth/login", json={"username": "operator", "password": "wrongpassword"})
    assert resp.status_code == 401

    # Missing token
    resp = client.get("/tasks/me/tasks")
    assert resp.status_code == 401

def test_rbac_and_task_authorization():
    # Operator Login
    resp = client.post("/auth/login", json={"username": "operator", "password": "demo123"})
    assert resp.status_code == 200
    op_token = resp.json()["access_token"]
    op_headers = {"Authorization": f"Bearer {op_token}"}

    # Operator gets own tasks
    resp = client.get("/tasks/me/tasks", headers=op_headers)
    assert resp.status_code == 200
    tasks = resp.json()
    assert len(tasks) > 0
    assert tasks[0]["operator_id"] == "11111111-1111-1111-1111-111111111111"

    # Operator accessing supervisor endpoint
    resp = client.get("/tasks/sites/SITE-A/tasks", headers=op_headers)
    assert resp.status_code == 403

    # Supervisor Login
    resp = client.post("/auth/login", json={"username": "supervisor", "password": "demo123"})
    assert resp.status_code == 200
    sup_token = resp.json()["access_token"]
    sup_headers = {"Authorization": f"Bearer {sup_token}"}

    # Supervisor accessing authorized site
    resp = client.get("/tasks/sites/SITE-A/tasks", headers=sup_headers)
    assert resp.status_code == 200

    # Supervisor accessing unauthorized site
    resp = client.get("/tasks/sites/SITE-B/tasks", headers=sup_headers)
    assert resp.status_code == 403

    # Admin Login
    resp = client.post("/auth/login", json={"username": "admin", "password": "demo123"})
    assert resp.status_code == 200
    admin_token = resp.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Admin accessing any site
    resp = client.get("/tasks/sites/SITE-B/tasks", headers=admin_headers)
    assert resp.status_code == 200

    # Admin accessing audit
    resp = client.get("/audit/verify", headers=admin_headers)
    assert resp.status_code == 200
    assert "valid" in resp.json()

    # Operator accessing audit
    resp = client.get("/audit/verify", headers=op_headers)
    assert resp.status_code == 403

@pytest.mark.asyncio
async def test_audit_tamper_evidence():
    async with AsyncSessionLocal() as session:
        # Clear existing audit log for test stability
        await session.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY"))
        await session.commit()
    
    async with AsyncSessionLocal() as session:
        # Append 3 rows
        for i in range(3):
            await append_audit_log(
                session=session,
                actor_id=f"test_actor_{i}",
                action="TEST_ACTION",
                target_type="TEST",
                target_id=f"target_{i}",
                payload={"data": i}
            )
        await session.commit()
        
    async with AsyncSessionLocal() as session:
        async with session.begin():
            is_valid, invalid_id, reason = await verify_chain(session)
            assert is_valid == True
        
        # Tamper with the second row (id=2)
        await session.execute(
            text("UPDATE audit_log SET payload_hash = 'tampered' WHERE id = 2")
        )
        await session.commit()
        
    async with AsyncSessionLocal() as session:
        async with session.begin():
            is_valid, invalid_id, reason = await verify_chain(session)
            assert is_valid == False
            assert invalid_id == 2
            assert "Row-level integrity check failed" in reason
