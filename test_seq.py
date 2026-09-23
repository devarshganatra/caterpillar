import asyncio
import httpx
from datetime import datetime, timezone
from contracts.events import TelemetryFrame
from contracts.signing import sign

async def test_seq():
    url = "http://localhost:8000/ingest/telemetry"
    secret = "secret1"
    
    def make_payload(seq):
        model = TelemetryFrame(
            seq=seq, ts=datetime.now(timezone.utc),
            machine_id="EXC001", operator_id="op", task_id="t",
            engine_rpm=1000, engine_temp_c=50, hydraulic_pressure_bar=20,
            fuel_rate_lph=10, speed_kmh=0, seatbelt="FASTENED",
            proximity=None, cycle_completed=False, truck_present=False,
            hauler_queue_len=0, gps=(0.0, 0.0), sig=""
        )
        payload = model.dict()
        payload["sig"] = sign(payload, secret)
        # convert datetime to string for httpx.post
        payload["ts"] = payload["ts"].isoformat()
        return payload
        
    async with httpx.AsyncClient() as client:
        r1 = await client.post(url, json=make_payload(10))
        assert r1.status_code == 202, f"Expected 202, got {r1.status_code}"
        assert r1.json()["result"] == "first", r1.json()
        
        r2 = await client.post(url, json=make_payload(10))
        assert r2.status_code == 202, f"Expected 200, got {r2.status_code}"
        assert r2.json()["result"] == "duplicate", r2.json()
        
        r3 = await client.post(url, json=make_payload(12))
        assert r3.status_code == 202, f"Expected 202, got {r3.status_code}"
        assert r3.json()["result"] == "gap", r3.json()
        
        r4 = await client.post(url, json=make_payload(11))
        assert r4.status_code == 409, f"Expected 409, got {r4.status_code}"
        
        # Test bad HMAC
        bad_payload = make_payload(13)
        bad_payload["sig"] = "bad"
        r5 = await client.post(url, json=bad_payload)
        assert r5.status_code == 401, f"Expected 401, got {r5.status_code}"

asyncio.run(test_seq())
print("Seq validation tests passed! ✓")
