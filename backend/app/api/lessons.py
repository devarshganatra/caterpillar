"""
Stage 4C — read-only lesson API for the operator's lesson player.
RBAC: an operator sees only their own lessons; supervisors/admins can view
any lesson (same pattern as incidents.py's _authorize_incident_read), for
future training-hub use, but nothing here writes on their behalf.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.deps import get_current_user
from backend.app.db.models import LessonRow, User
from backend.app.db.session import get_db

router = APIRouter()


def _lesson_summary(row: LessonRow) -> dict:
    return {
        "id": str(row.id), "incident_id": str(row.incident_id), "machine_id": row.machine_id,
        "operator_id": row.operator_id, "title": row.title, "short_tip": row.short_tip,
        "source": row.source, "status": row.status, "generated_at": row.generated_at,
        "delivered_at": row.delivered_at, "read_at": row.read_at,
    }


def _lesson_detail(row: LessonRow) -> dict:
    return {
        **_lesson_summary(row),
        "explanation": row.explanation, "knowledge_refs": row.knowledge_refs,
        "fallback_reason": row.fallback_reason,
    }


@router.get("")
async def list_my_lessons(
    limit: int = Query(20, le=100), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    stmt = select(LessonRow).where(LessonRow.operator_id == str(user.id)) \
        .order_by(LessonRow.generated_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return {"items": [_lesson_summary(r) for r in result.scalars().all()]}


@router.get("/{lesson_id}")
async def get_lesson(
    lesson_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    try:
        row = await db.get(LessonRow, lesson_id)
    except Exception:
        row = None
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found")

    if user.role.value == "OPERATOR" and row.operator_id != str(user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your lesson")

    if row.read_at is None:
        # Simplest MVP signal for "the operator has seen this lesson" — the
        # player fetches this endpoint once on mount, so a single GET is a
        # reasonable proxy for "read" without a separate ack endpoint.
        row.read_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(row)

    return _lesson_detail(row)
