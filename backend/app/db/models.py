"""
SQLAlchemy ORM models for CAT Co-Pilot.

Design notes:
- machine_state_log uses (ts, machine_id) composite PK — TimescaleDB hypertable-ready.
  No future migration needed to convert it; just call create_hypertable().
- events uses (machine_id, seq) unique constraint for idempotency:
  a re-delivered Redis message with the same (machine_id, seq) will be safely rejected
  via ON CONFLICT DO NOTHING rather than creating a duplicate row.
- UUIDs are generated server-side (uuid_generate_v4() via sqlalchemy default=func.gen_random_uuid()).
- All timestamps are TIMESTAMPTZ (timezone-aware).
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean,
    DateTime, ForeignKey, UniqueConstraint, Index, func, Identity, text
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase
import enum
from sqlalchemy import Enum as SQLAlchemyEnum

class RoleEnum(str, enum.Enum):
    OPERATOR = "OPERATOR"
    SUPERVISOR = "SUPERVISOR"
    ADMIN = "ADMIN"

class TaskStatusEnum(str, enum.Enum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"


class Base(DeclarativeBase):
    pass


class Machine(Base):
    __tablename__ = "machines"

    id = Column(String(32), primary_key=True)   # e.g. "EXC001"
    site_id = Column(String(32), nullable=False)
    machine_type = Column(String(16), nullable=False)  # EXCAVATOR | LOADER
    model = Column(String(32), nullable=False)


class Event(Base):
    """
    One event = one deterministic rule firing (seatbelt, proximity, health, overspeed).
    Idempotency key: (machine_id, seq) — same sequence number from the same machine
    cannot produce two different DB rows for the same event type.
    We use (machine_id, seq, type) as the uniqueness constraint because one frame
    may theoretically produce multiple event types (e.g. seatbelt + overspeed).
    """
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(String(64), nullable=False)
    severity = Column(String(16), nullable=False)  # INFO | WARNING | CRITICAL
    machine_id = Column(String(32), ForeignKey("machines.id"), nullable=False)
    operator_id = Column(String(64), nullable=False)
    site_id = Column(String(32), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False)
    source_engine = Column(String(64), nullable=False)
    evidence = Column(JSONB, nullable=False, default=dict)

    # Idempotency: one (machine, frame-seq, event-type) → at most one DB row
    frame_seq = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("machine_id", "frame_seq", "type",
                         name="uq_events_machine_seq_type"),
        Index("ix_events_machine_ts", "machine_id", "ts"),
    )


class Alert(Base):
    """
    Active/historical alert windows. Key = (machine_id, type, zone).
    Updated in-place while the condition holds; a new row is created when
    the condition clears and re-triggers.
    """
    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(128), nullable=False)   # "{machine_id}:{type}:{zone}"
    machine_id = Column(String(32), ForeignKey("machines.id"), nullable=False)
    first_ts = Column(DateTime(timezone=True), nullable=False)
    last_ts = Column(DateTime(timezone=True), nullable=False)
    count = Column(Integer, nullable=False, default=1)
    acked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_alerts_machine_key", "machine_id", "key"),
    )


class MachineStateLog(Base):
    """
    Time-series log of machine state snapshots from the hot worker.
    Composite PK (ts, machine_id) makes this a clean TimescaleDB hypertable candidate.
    One row per processed telemetry frame (1 Hz → ~86400 rows/machine/day).
    """
    __tablename__ = "machine_state_log"

    ts = Column(DateTime(timezone=True), primary_key=True, nullable=False)
    machine_id = Column(String(32), ForeignKey("machines.id"), primary_key=True, nullable=False)
    state = Column(String(16), nullable=False)        # OFF | IDLE | TRAVEL | WORKING
    risk_score = Column(Float, nullable=False, default=0.0)
    risk_level = Column(String(16), nullable=False, default="NORMAL")
    frame_seq = Column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_state_log_machine_ts", "machine_id", "ts"),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(64), unique=True, nullable=False)
    hashed_password = Column(String(256), nullable=False)
    role = Column(SQLAlchemyEnum(RoleEnum), nullable=False)
    name = Column(String(128), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)


class UserSiteAccess(Base):
    __tablename__ = "user_site_access"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True, nullable=False)
    site_id = Column(String(32), primary_key=True, nullable=False)

    # Note: the composite primary key naturally enforces the uniqueness constraint


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(64), primary_key=True)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    machine_id = Column(String(32), ForeignKey("machines.id"), nullable=False)
    site_id = Column(String(32), nullable=False)
    status = Column(SQLAlchemyEnum(TaskStatusEnum), nullable=False, default=TaskStatusEnum.PLANNED)
    est_duration_minutes = Column(Integer, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, Identity(start=1, cycle=False), primary_key=True)
    actor_id = Column(String(64), nullable=False) # String since it could be SYSTEM or a UUID
    action = Column(String(64), nullable=False)
    target_type = Column(String(64), nullable=False)
    target_id = Column(String(128), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    prev_hash = Column(String(64), nullable=True)
    current_hash = Column(String(64), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("current_hash", name="uq_audit_log_current_hash"),
    )
