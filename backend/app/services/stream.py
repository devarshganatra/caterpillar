from typing import Any, Dict
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
        data = flatten_context(frame)
        data['sig'] = frame.sig or ""
        
        # Publish to stream
        await redis_client.xadd("context:main", data)
        # Latest snapshot per site
        if frame.site_id:
            await redis_client.hset(f"context:{frame.site_id}", mapping=data)
            
        return True
    except RedisError as e:
        logger.error(f"Failed to publish context to Redis: {e}")
        return False
