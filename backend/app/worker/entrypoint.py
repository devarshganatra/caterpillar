import asyncio
import logging
import sys

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.worker.hot import MachineHotState, process_frame, flush_buffers
from contracts.machine_config import MACHINES
from contracts.events import MachineState

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

async def init_consumer_group(redis_client: Redis, stream_key: str, group_name: str):
    try:
        await redis_client.xgroup_create(stream_key, group_name, id="0", mkstream=True)
        logger.info(f"Created consumer group {group_name} on {stream_key}")
    except ResponseError as e:
        if "BUSYGROUP Consumer Group name already exists" in str(e):
            pass
        else:
            raise

async def restore_hot_states(redis_client: Redis) -> dict[str, MachineHotState]:
    states = {}
    for mid in MACHINES.keys():
        states[mid] = MachineHotState(machine_id=mid)
        # Try to restore from Redis
        raw = await redis_client.hgetall(f"machine_state:{mid}")
        if raw:
            try:
                if b'last_seq' in raw:
                    states[mid].last_seq = int(raw[b'last_seq'])
                if b'state' in raw:
                    states[mid].state = MachineState(raw[b'state'].decode())
                if b'risk_score' in raw:
                    states[mid].risk_score = float(raw[b'risk_score'])
                if b'risk_level' in raw:
                    states[mid].risk_level = raw[b'risk_level'].decode()
                logger.info(f"Restored state for {mid}: seq={states[mid].last_seq}, state={states[mid].state.value}")
            except Exception as e:
                logger.warning(f"Failed to restore state for {mid}: {e}")
                
    return states

async def consume_shard(
    shard_index: int, 
    hot_states: dict[str, MachineHotState],
    redis_client: Redis
):
    stream_key = f"telemetry:{shard_index}"
    group_name = "hot-worker"
    consumer_name = f"w-{shard_index}"
    
    await init_consumer_group(redis_client, stream_key, group_name)
    
    logger.info(f"Started consumer {consumer_name} on {stream_key}")
    
    # 1. Process pending messages (from crashed workers)
    # Simple approach for now: read from 0-0 once
    try:
        pending = await redis_client.xreadgroup(group_name, consumer_name, {stream_key: "0-0"}, count=100)
        if pending:
            logger.info(f"Found {len(pending[0][1])} pending messages on {stream_key}")
            for _, messages in pending:
                for msg_id, data in messages:
                    async with AsyncSessionLocal() as session:
                        mid = data.get(b'machine_id', b'').decode()
                        if mid in hot_states:
                            await process_frame(data, msg_id, hot_states[mid], redis_client, session, stream_key, group_name)
                            await flush_buffers(session, mid, hot_states[mid], redis_client, stream_key, group_name)
    except Exception as e:
        logger.error(f"Error processing pending messages on {stream_key}: {e}")

    # 2. Main loop
    while True:
        try:
            msgs = await redis_client.xreadgroup(
                group_name, consumer_name, 
                {stream_key: ">"}, 
                count=50, block=2000
            )
            
            if not msgs:
                # Idle, force flush any lingering buffers
                for state in hot_states.values():
                    if state.event_buffer or state.state_log_buffer or state.unacked_msg_ids:
                        async with AsyncSessionLocal() as session:
                            await flush_buffers(session, state.machine_id, state, redis_client, stream_key, group_name)
                continue
                
            async with AsyncSessionLocal() as session:
                for stream_name, messages in msgs:
                    for msg_id, data in messages:
                        mid = data.get(b'machine_id', b'').decode()
                        if mid in hot_states:
                            await process_frame(data, msg_id, hot_states[mid], redis_client, session, stream_key, group_name)
                        else:
                            logger.warning(f"Unknown machine_id {mid} in stream {stream_name}")
                            # Acknowledge it anyway so it doesn't get stuck forever
                            await redis_client.xack(stream_key, group_name, msg_id)
                
        except Exception as e:
            logger.error(f"Consumer loop error on {stream_key}: {e}")
            await asyncio.sleep(1.0)


async def main():
    logger.info("Starting hot worker...")
    redis_client = Redis.from_url(settings.redis_url)
    
    # Check connectivity
    await redis_client.ping()
    
    hot_states = await restore_hot_states(redis_client)
    
    tasks = []
    # 4 shards based on architecture
    for i in range(4):
        tasks.append(asyncio.create_task(consume_shard(i, hot_states, redis_client)))
        
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Worker shutting down")
    finally:
        await redis_client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
