from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from backend.app.db.session import get_db
from backend.app.db.models import Task, User
from backend.app.api.deps import get_current_user, require_roles, verify_site_access

router = APIRouter()

@router.get("/me/tasks")
async def get_my_tasks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Task).where(Task.operator_id == current_user.id)
    )
    tasks = result.scalars().all()
    # Pydantic serialization can be left to FastAPI response processing if we had schemas, 
    # but for simplicity we return dicts.
    return [
        {
            "id": t.id,
            "operator_id": str(t.operator_id),
            "machine_id": t.machine_id,
            "site_id": t.site_id,
            "status": t.status.value,
            "est_duration_minutes": t.est_duration_minutes
        }
        for t in tasks
    ]

@router.get("/sites/{site_id}/tasks")
async def get_site_tasks(
    site_id: str,
    current_user: User = Depends(require_roles(["SUPERVISOR", "ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    has_access = await verify_site_access(site_id, current_user, db)
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="You do not have access to this site's tasks"
        )
        
    result = await db.execute(
        select(Task).where(Task.site_id == site_id)
    )
    tasks = result.scalars().all()
    return [
        {
            "id": t.id,
            "operator_id": str(t.operator_id),
            "machine_id": t.machine_id,
            "site_id": t.site_id,
            "status": t.status.value,
            "est_duration_minutes": t.est_duration_minutes
        }
        for t in tasks
    ]
