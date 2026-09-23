from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any

from backend.app.db.session import get_db
from backend.app.api.deps import require_roles, User
from backend.app.services.audit_chain import verify_chain

router = APIRouter()

@router.get("/verify")
async def verify_audit_chain(
    current_user: User = Depends(require_roles(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    # Run the verification (SQLAlchemy auto-begins a transaction on first query)
    is_valid, first_invalid_id, reason = await verify_chain(db)
        
    if is_valid:
        return {"valid": True}
    else:
        return {
            "valid": False,
            "first_invalid_id": first_invalid_id,
            "reason": reason
        }
