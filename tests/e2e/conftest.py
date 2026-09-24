from datetime import datetime, timezone

import pytest
from redis.asyncio import Redis

from backend.app.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import (
    WindowAggregate, EtaEstimateRow, Event as DBEvent, IncidentRow,
    IncidentEvent, IncidentTimeline, IncidentExplanationRow, LessonRow,
)
from backend.app.services import stream as stream_module
from contracts.demo_assignments import get_assignment
from sqlalchemy import delete, select

MACHINE = "EXC001"
SITE = "SITE-A"


@pytest.fixture
async def redis_client():
    client = Redis.from_url(settings.redis_url)
    stream_module.redis_client = client
    yield client
    stream_module.redis_client = None
    await client.aclose()


@pytest.fixture
async def clean_machine():
    async def _clean():
        async with AsyncSessionLocal() as session:
            incidents = (await session.execute(
                select(IncidentRow.id).where(IncidentRow.machine_id == MACHINE)
            )).scalars().all()
            for iid in incidents:
                # lessons (Stage 4A) FK-references incidents.id too.
                await session.execute(delete(LessonRow).where(LessonRow.incident_id == iid))
                await session.execute(delete(IncidentExplanationRow).where(IncidentExplanationRow.incident_id == iid))
                await session.execute(delete(IncidentTimeline).where(IncidentTimeline.incident_id == iid))
                await session.execute(delete(IncidentEvent).where(IncidentEvent.incident_id == iid))
            await session.execute(delete(IncidentRow).where(IncidentRow.machine_id == MACHINE))
            await session.execute(delete(DBEvent).where(DBEvent.machine_id == MACHINE))
            await session.execute(delete(WindowAggregate).where(WindowAggregate.machine_id == MACHINE))
            await session.execute(delete(EtaEstimateRow).where(EtaEstimateRow.task_id == get_assignment(MACHINE).task_id))
            await session.commit()
    await _clean()
    yield
    await _clean()
