"""Admin/observability endpoints (Stage 3 Batch 3F)."""
from fastapi import APIRouter, Depends

from backend.app.api.deps import require_roles
from backend.app.db.models import User

router = APIRouter()

STREAMS_GROUPS = [
    ("telemetry:{shard}", "hot-worker", 4),
    ("telemetry:{shard}", "warm-worker", 4),
    ("events:{shard}", "correlator", 4),
]


@router.get("/workers")
async def get_worker_status(user: User = Depends(require_roles(["ADMIN"]))):
    from backend.app.services import stream as stream_module

    redis_client = stream_module.redis_client
    workers = {}
    lag = {}

    if redis_client is not None:
        for name in ("hot", "warm", "correlator", "cold"):
            raw = await redis_client.hgetall(f"worker:status:{name}")
            if raw:
                workers[name] = {k.decode(): v.decode() for k, v in raw.items()}

        for stream_pattern, group_name, n_shards in STREAMS_GROUPS:
            for i in range(n_shards):
                stream_key = stream_pattern.format(shard=i)
                try:
                    groups = await redis_client.xinfo_groups(stream_key)
                    for g in groups:
                        if g.get(b"name", b"").decode() == group_name:
                            lag[f"{stream_key}/{group_name}"] = {
                                "pending": g.get(b"pending"), "lag": g.get(b"lag"),
                                "entries_read": g.get(b"entries-read"),
                            }
                except Exception:
                    continue  # stream/group may not exist yet (e.g. no traffic so far)

    return {"workers": workers, "stream_lag": lag}
