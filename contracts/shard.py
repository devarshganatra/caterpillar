import hashlib

NUM_SHARDS = 4

def shard_for(machine_id: str) -> int:
    """
    SHA-256(machine_id.encode()) → big-endian int → % NUM_SHARDS
    Deterministic across processes and restarts.
    """
    digest = hashlib.sha256(machine_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="big") % NUM_SHARDS

def stream_key(machine_id: str) -> str:
    return f"telemetry:{shard_for(machine_id)}"
