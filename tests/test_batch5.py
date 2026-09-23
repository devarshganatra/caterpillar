import pytest
import asyncio
from httpx import ASGITransport, AsyncClient
from backend.app.main import app
from jose import jwt
from backend.app.config import settings

@pytest.mark.asyncio
async def test_ws_ticket_generation_and_me_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Login as operator
        resp = await client.post("/auth/login", json={"username": "operator", "password": "demo123"})
        assert resp.status_code == 200
        op1_token = resp.json()["access_token"]

        headers = {"Authorization": f"Bearer {op1_token}"}

        # 2. Get /me
        resp = await client.get("/auth/me", headers=headers)
        assert resp.status_code == 200
        me_data = resp.json()
        assert me_data["username"] == "operator"
        assert "SITE-A" in me_data["site_ids"]

        # 3. Get WS Ticket
        resp = await client.post("/auth/ws-ticket", headers=headers)
        assert resp.status_code == 200
        ticket = resp.json()["ticket"]
        
        # Verify ticket payload
        payload = jwt.decode(ticket, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        assert payload["type"] == "ws_ticket"
        assert payload["sub"] == me_data["id"]

@pytest.mark.asyncio
async def test_ws_security_operator_scoping():
    # In a full test we would test the WebSocket endpoint directly,
    # but Starlette's TestClient websocket context manager is sync and httpx async client 
    # doesn't support WebSockets natively yet without additional libs.
    # We will verify the authorization logic using the fastapi TestClient just for the WS.
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    # 1. Login operator
    resp = client.post("/auth/login", json={"username": "operator", "password": "demo123"})
    op1_token = resp.json()["access_token"]
    
    # Get ticket for op1
    resp = client.post("/auth/ws-ticket", headers={"Authorization": f"Bearer {op1_token}"})
    op1_ticket = resp.json()["ticket"]
    
    # 2. Try to connect to EXC001 (which is at SITE-A, op1 has access)
    # The WS route accepts it. If it accepts it, we can receive/close.
    try:
        with client.websocket_connect(f"/ws/stream/EXC001?ticket={op1_ticket}") as websocket:
            pass # Authorized
    except Exception as e:
        # In Starlette TestClient, a 1008 close raises an exception
        pytest.fail(f"Authorized WS connection failed: {e}")

    # 3. Login supervisor (has access to SITE-A, but NOT SITE-B)
    resp = client.post("/auth/login", json={"username": "supervisor", "password": "demo123"})
    op3_token = resp.json()["access_token"]
    
    resp = client.post("/auth/ws-ticket", headers={"Authorization": f"Bearer {op3_token}"})
    op3_ticket = resp.json()["ticket"]

    # Try to connect supervisor to EXC003 (EXC003 is physically in SITE-B, and supervisor only has access to SITE-A)
    # The WS route should securely reject it based on genuine database RBAC rules!
    from fastapi.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/ws/stream/EXC003?ticket={op3_ticket}") as websocket:
            websocket.receive_text()
    assert excinfo.value.code == 1008
