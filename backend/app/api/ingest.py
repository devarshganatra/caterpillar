from fastapi import APIRouter, HTTPException, Request, status, Depends
from datetime import datetime, timezone
import logging

from contracts.events import TelemetryFrame, ContextFrame, SeqValidationResult
from contracts.signing import verify
from contracts.shard import stream_key
from backend.app.config import settings
from backend.app.services import stream

logger = logging.getLogger(__name__)

router = APIRouter()

SEQ_VALIDATE_LUA = """
local key = KEYS[1]
local new_seq = tonumber(ARGV[1])
local last = tonumber(redis.call('GET', key))

if not last then
    redis.call('SET', key, new_seq)
    return 'first'
end
if new_seq == last then return 'duplicate' end
if new_seq < last then return 'out_of_order' end
if new_seq > last + 1 then
    redis.call('SET', key, new_seq)
    return 'gap'
end
redis.call('SET', key, new_seq)
return 'accepted'
"""

async def atomic_seq_validate(machine_id: str, seq: int) -> SeqValidationResult:
    if not stream.redis_client:
        # Fallback if redis is somehow missing
        return SeqValidationResult.ACCEPTED
    
    key = f"telemetry:seq:{machine_id}"
    try:
        result_bytes = await stream.redis_client.eval(SEQ_VALIDATE_LUA, 1, key, seq)
        result_str = result_bytes.decode('utf-8')
        return SeqValidationResult(result_str)
    except Exception as e:
        logger.error(f"Redis Lua eval failed: {e}")
        # Default to accepted to not block flow if redis seq fails but stream is up, 
        # though realistically if redis is down, stream publish will fail anyway.
        return SeqValidationResult.ACCEPTED

@router.post("/telemetry", status_code=status.HTTP_202_ACCEPTED)
async def ingest_telemetry(frame: TelemetryFrame):
    # 1. Verify HMAC
    secret = settings.parsed_machine_hmac_keys.get(frame.machine_id)
    if not secret:
        raise HTTPException(status_code=401, detail="Unknown machine_id or missing key")
    
    # We must convert to dict and exclude unset fields if any, but our model is complete.
    if not verify(frame.model_dump(), secret):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")
        
    # 2. Timestamp freshness (Replay defense)
    now = datetime.now(timezone.utc)
    delta = abs((frame.ts - now).total_seconds())
    if delta > 30:
        raise HTTPException(status_code=422, detail="Timestamp freshness check failed")

    # 3. Atomic sequence validation
    seq_result = await atomic_seq_validate(frame.machine_id, frame.seq)
    
    if seq_result == SeqValidationResult.OUT_OF_ORDER:
        logger.warning(f"OUT_OF_ORDER sequence for {frame.machine_id}: {frame.seq}")
        raise HTTPException(status_code=409, detail="Sequence out of order")
        
    elif seq_result == SeqValidationResult.DUPLICATE:
        # Idempotent ignore
        return {"result": seq_result.value, "shard": stream_key(frame.machine_id)}
        
    elif seq_result == SeqValidationResult.GAP:
        logger.warning(f"GAP in sequence for {frame.machine_id}: current {frame.seq}")

    # 4. Publish to Redis
    ok = await stream.publish_telemetry(frame)
    if not ok:
        raise HTTPException(status_code=503, detail="Failed to publish to stream")
        
    return {"result": seq_result.value, "shard": stream_key(frame.machine_id)}


@router.post("/context", status_code=status.HTTP_202_ACCEPTED)
async def ingest_context(frame: ContextFrame):
    # ContextFrame has no sig field — context is pushed by trusted internal services
    # (simulator, site GW) and authenticated implicitly. HMAC for context deferred to Batch 4.
    ok = await stream.publish_context(frame)
    if not ok:
        raise HTTPException(status_code=503, detail="Failed to publish context to stream")
        
    return {"result": "ACCEPTED"}
