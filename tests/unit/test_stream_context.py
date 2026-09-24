"""Regression test: publish_context must not crash on ContextFrame (which has no `sig` field)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.app.services import stream
from contracts.events import ContextFrame

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_publish_context_does_not_access_missing_sig_attr(monkeypatch):
    fake_redis = AsyncMock()
    fake_redis.xadd = AsyncMock()
    fake_redis.hset = AsyncMock()
    monkeypatch.setattr(stream, "redis_client", fake_redis)

    frame = ContextFrame(
        ts=datetime.now(timezone.utc), site_id="SITE-A", weather="RAIN",
        rainfall_mm_h=12.0, visibility_m=180.0, ambient_temp_c=18.0,
        wind_kmh=10.0, ground="MUDDY", daylight=True,
    )

    ok = await stream.publish_context(frame)
    assert ok is True
    fake_redis.xadd.assert_awaited_once()
    fake_redis.hset.assert_awaited_once()


def test_parse_flat_context_roundtrips_flatten_context():
    frame = ContextFrame(
        ts=datetime.now(timezone.utc), site_id="SITE-A", weather="RAIN",
        rainfall_mm_h=12.0, visibility_m=180.0, ambient_temp_c=18.0,
        wind_kmh=10.0, ground="MUDDY", daylight=True,
    )
    flat = stream.flatten_context(frame)
    raw = {k.encode(): str(v).encode() for k, v in flat.items()}
    parsed = stream.parse_flat_context(raw)
    assert parsed is not None
    assert parsed.site_id == "SITE-A"
    assert parsed.weather == "RAIN"
    assert parsed.daylight is True


def test_parse_flat_context_empty_returns_none():
    assert stream.parse_flat_context({}) is None
