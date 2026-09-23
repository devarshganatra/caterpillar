import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import text, desc, asc
from typing import Dict, Any, Tuple, Optional

from backend.app.db.models import AuditLog

def get_canonical_json(data: Dict[str, Any]) -> str:
    """Returns canonical JSON representation of a dict."""
    return json.dumps(data, sort_keys=True, separators=(',', ':'))

def sha256_hash(content: str) -> str:
    """Returns SHA-256 hex digest."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()

def compute_current_hash(
    prev_hash: Optional[str],
    payload_hash: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    ts: datetime
) -> str:
    """Computes the deterministic current_hash for an audit row."""
    # Convert ts to strict ISO format, e.g., 2026-09-23T16:52:52.123456Z
    ts_canonical = ts.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    canonical_dict = {
        "prev_hash": prev_hash,
        "payload_hash": payload_hash,
        "actor_id": actor_id,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "ts": ts_canonical
    }
    return sha256_hash(get_canonical_json(canonical_dict))

async def append_audit_log(
    session: AsyncSession,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    payload: Dict[str, Any]
) -> AuditLog:
    """
    Appends a new entry to the audit log safely using an advisory lock.
    """
    # 1. Acquire transaction-level advisory lock
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('audit-chain-global'))"))
    
    # 2. Find the latest entry
    result = await session.execute(
        select(AuditLog.current_hash).order_by(desc(AuditLog.id)).limit(1)
    )
    latest_hash = result.scalars().first()
    # If no latest_hash exists, this is the Genesis entry, so prev_hash = None
    prev_hash = latest_hash
    
    # 3. Compute hashes
    payload_hash = sha256_hash(get_canonical_json(payload))
    ts_now = datetime.now(timezone.utc)
    
    current_hash = compute_current_hash(
        prev_hash=prev_hash,
        payload_hash=payload_hash,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ts=ts_now
    )
    
    # 4. Insert
    new_entry = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload_hash=payload_hash,
        prev_hash=prev_hash,
        current_hash=current_hash,
        ts=ts_now
    )
    session.add(new_entry)
    # We must flush to get the autoincremented ID and ensure it is saved
    await session.flush()
    
    return new_entry

async def verify_chain(session: AsyncSession) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    Verifies the integrity of the audit chain.
    Returns (is_valid, first_invalid_id, reason).
    """
    # Use read-only snapshot isolation implicit within the current transaction context
    result = await session.execute(
        select(AuditLog).order_by(asc(AuditLog.id))
    )
    logs = result.scalars().all()
    
    expected_prev_hash = None
    
    for i, log in enumerate(logs):
        # 1. Genesis entry check
        if i == 0:
            if log.prev_hash is not None:
                return False, log.id, "Genesis entry must have prev_hash = NULL"
        else:
            # 2. Link check
            if log.prev_hash != expected_prev_hash:
                return False, log.id, f"Broken link: expected prev_hash {expected_prev_hash}, got {log.prev_hash}"
                
        # 3. Row-level integrity check
        recomputed_hash = compute_current_hash(
            prev_hash=log.prev_hash,
            payload_hash=log.payload_hash,
            actor_id=log.actor_id,
            action=log.action,
            target_type=log.target_type,
            target_id=log.target_id,
            ts=log.ts
        )
        
        if recomputed_hash != log.current_hash:
            return False, log.id, "Row-level integrity check failed: current_hash mismatch"
            
        expected_prev_hash = log.current_hash
        
    return True, None, None
