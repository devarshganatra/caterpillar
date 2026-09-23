from typing import Any, Dict
from datetime import datetime, timezone
from redis.asyncio import Redis
from redis.exceptions import RedisError
import logging

from contracts.events import TelemetryFrame, ContextFrame
from contracts.shard import stream_key

logger = logging.getLogger(__name__)

# This will be initialized in main.py lifespan
redis_client: Redis | None = None

def _flatten_dict(d: dict, parent_key: str = '') -> dict:
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}_{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key).items())
        elif isinstance(v, list):
            items.append((new_key, str(v)))
        elif isinstance(v, bool):
            items.append((new_key, "true" if v else "false"))
        elif v is None:
            items.append((new_key, ""))
        else:
            items.append((new_key, str(v)))
    return dict(items)

def flatten_telemetry(frame: TelemetryFrame) -> dict:
    return _flatten_dict(frame.model_dump(exclude={"sig"}))

def flatten_context(frame: ContextFrame) -> dict:
    # ContextFrame has no sig field
    return _flatten_dict(frame.model_dump())


def parse_flat_context(raw: dict) -> ContextFrame | None:
    """
    Inverse of flatten_context: reconstructs a ContextFrame from the flat
    bytes dict stored by HSET context:{site_id}. Returns None if the hash is
    empty or missing required fields (caller should keep the previous
    snapshot in that case).
    """
    if not raw:
        return None

    def _val(k_str, default=None, typ=str):
        k = k_str.encode("utf-8")
        if k not in raw:
            return default
        v = raw[k]
        if typ == bool:
            return v == b"true"
        if typ == float:
            return float(v)
        return v.decode("utf-8")

    ts_str = _val("ts")
    if not ts_str:
        return None
    ts = datetime.fromisoformat(ts_str)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    try:
        return ContextFrame(
            ts=ts,
            site_id=_val("site_id"),
            weather=_val("weather"),
            rainfall_mm_h=_val("rainfall_mm_h", default=0.0, typ=float),
            visibility_m=_val("visibility_m", default=9999.0, typ=float),
            ambient_temp_c=_val("ambient_temp_c", default=20.0, typ=float),
            wind_kmh=_val("wind_kmh", default=0.0, typ=float),
            ground=_val("ground"),
            daylight=_val("daylight", default=True, typ=bool),
        )
    except Exception as e:
        logger.warning(f"Failed to parse context hash: {e}")
        return None

async def publish_telemetry(frame: TelemetryFrame) -> bool:
    if not redis_client:
        return False
    try:
        data = flatten_telemetry(frame)
        data['sig'] = frame.sig or "" # ensure sig is in the stream if needed by workers for audit
        await redis_client.xadd(stream_key(frame.machine_id), data)
        return True
    except RedisError as e:
        logger.error(f"Failed to publish telemetry to Redis: {e}")
        return False

async def publish_context(frame: ContextFrame) -> bool:
    if not redis_client:
        return False
    try:
        # ContextFrame has no sig field (see ingest.py: context is not yet
        # HMAC-authenticated). Accessing frame.sig raised AttributeError and
        # 500'd every /ingest/context call, so context was never actually
        # reaching Redis before this fix.
        data = flatten_context(frame)

        # Publish to stream
        await redis_client.xadd("context:main", data)
        # Latest snapshot per site
        if frame.site_id:
            await redis_client.hset(f"context:{frame.site_id}", mapping=data)
            
        return True
    except RedisError as e:
        logger.error(f"Failed to publish context to Redis: {e}")
        return False
