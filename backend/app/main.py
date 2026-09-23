from fastapi import FastAPI
from contextlib import asynccontextmanager
import redis.asyncio as redis
import logging

from backend.app.config import settings
from backend.app.services import stream
from backend.app.api.ingest import router as ingest_router
from backend.app.api.ws import router as ws_router
from backend.app.api.auth import router as auth_router
from backend.app.api.tasks import router as tasks_router
from backend.app.api.audit import router as audit_router
from backend.app.api.incidents import router as incidents_router
from backend.app.api.machines import router as machines_router
from backend.app.api.admin import router as admin_router
from backend.app.api.knowledge import router as knowledge_router

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info(f"Connecting to Redis at {settings.redis_url}")
    stream.redis_client = redis.from_url(settings.redis_url)
    yield
    # Shutdown
    if stream.redis_client:
        await stream.redis_client.close()

app = FastAPI(title="CAT Co-Pilot API", lifespan=lifespan)

app.include_router(ingest_router, prefix="/ingest", tags=["ingest"])
app.include_router(ws_router, prefix="/ws", tags=["ws"])
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(tasks_router, prefix="/tasks", tags=["tasks"])
app.include_router(audit_router, prefix="/audit", tags=["audit"])
app.include_router(incidents_router, prefix="/incidents", tags=["incidents"])
app.include_router(machines_router, prefix="/machines", tags=["machines"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])
app.include_router(knowledge_router, prefix="/knowledge", tags=["knowledge"])

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.get("/ready")
async def readiness_check():
    # Example readiness check: verify Redis is reachable
    if stream.redis_client:
        try:
            await stream.redis_client.ping()
            return {"status": "ready"}
        except Exception:
            return {"status": "unready", "detail": "Redis unavailable"}
    return {"status": "unready"}
