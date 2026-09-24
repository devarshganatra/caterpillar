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
    # Stage 3: operator skill level, used by ETA/idle-attribution features.
    # NULL means unknown -> serving layer defaults to INTERMEDIATE.
    skill_level = Column(String(16), nullable=True)  # EXPERT | INTERMEDIATE | BEGINNER


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
    # Stage 3: ETA prediction needs these; NULL means "no ETA available for this task"
    # (warm.py checks for this explicitly rather than guessing values).
    task_type = Column(String(32), nullable=True)  # matches ml.constants.TASK_TYPE_NAMES
    target_cycles = Column(Integer, nullable=True)
    planned_start = Column(DateTime(timezone=True), nullable=True)


class WindowAggregate(Base):
    """
    One row per 30s (configurable) event-time window per machine, produced
    by the warm worker (Batch 3D). window_id is deterministic
    (contracts.ids.window_id), so ON CONFLICT DO NOTHING makes closing the
    same window twice (e.g. after a worker restart) a no-op.
    """
    __tablename__ = "window_aggregates"

    window_id = Column(String(64), primary_key=True)
    machine_id = Column(String(32), ForeignKey("machines.id"), nullable=False)
    site_id = Column(String(32), nullable=False)
    operator_id = Column(String(64), nullable=False)
    task_id = Column(String(64), nullable=True)
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    frame_count = Column(Integer, nullable=False, default=0)
    first_seq = Column(Integer, nullable=True)
    last_seq = Column(Integer, nullable=True)
    late_frames = Column(Integer, nullable=False, default=0)

    # WINDOW_FEATURES (ml.constants) + a few descriptive extras; NULL when
    # the window had too few frames to compute a feature.
    fuel_per_cycle = Column(Float, nullable=True)
    rpm_mean = Column(Float, nullable=True)
    rpm_std = Column(Float, nullable=True)
    hyd_p95 = Column(Float, nullable=True)
    idle_ratio = Column(Float, nullable=True)
    cycle_count = Column(Integer, nullable=True)
    cycle_time_mean = Column(Float, nullable=True)
    cycle_time_cv = Column(Float, nullable=True)
    temp_slope = Column(Float, nullable=True)
    working_ratio = Column(Float, nullable=True)
    truck_present_ratio = Column(Float, nullable=True)
    hauler_queue_mean = Column(Float, nullable=True)
    fuel_l_total = Column(Float, nullable=True)

    alerts_count = Column(Integer, nullable=False, default=0)
    context = Column(JSONB, nullable=True)
    idle_attribution = Column(JSONB, nullable=True)
    anomaly = Column(JSONB, nullable=True)
    # Deviation raw-flag bookkeeping for operator_deviation()'s persistence
    # rule (>= idle_dev_persist_windows CONSECUTIVE raw flags). Not part of
    # any contract; internal warm-worker state, restart-safe via the DB.
    deviation_raw_flag = Column(Boolean, nullable=False, default=False)
    deviation_consecutive = Column(Integer, nullable=False, default=0)
    last_eta_slip_band = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_window_aggregates_machine_start", "machine_id", "window_start"),
    )


class EtaEstimateRow(Base):
    """
    One row per ETA computation. kind='BASELINE' is the task-start prediction
    (at most one per task, enforced by uq_eta_baseline_per_task); kind='LIVE'
    is the blended live estimate, one per (task, window).
    """
    __tablename__ = "eta_estimates"

    id = Column(Integer, Identity(start=1, cycle=False), primary_key=True)
    task_id = Column(String(64), ForeignKey("tasks.id"), nullable=False)
    machine_id = Column(String(32), nullable=False)
    window_id = Column(String(64), nullable=True)
    ts = Column(DateTime(timezone=True), nullable=False)
    kind = Column(String(16), nullable=False)  # BASELINE | LIVE
    payload = Column(JSONB, nullable=False)    # full EtaEstimate
    model_version = Column(String(64), nullable=True)

    __table_args__ = (
        # Postgres treats NULL != NULL, so a plain UNIQUE(task_id, window_id,
        # kind) would NOT stop two BASELINE rows (window_id always NULL for
        # BASELINE) for the same task — hence the separate partial index.
        UniqueConstraint("task_id", "window_id", "kind", name="uq_eta_task_window_kind"),
        Index("uq_eta_baseline_per_task", "task_id", unique=True,
              postgresql_where=text("kind = 'BASELINE'")),
        Index("ix_eta_estimates_task_ts", "task_id", "ts"),
    )


class IncidentRow(Base):
    """
    One row per correlated incident. id is deterministic
    (contracts.ids.incident_id(machine_id, trigger_event_id)), so opening
    the same incident twice (e.g. reprocessed events after a restart) is a
    no-op via ON CONFLICT DO NOTHING.
    """
    __tablename__ = "incidents"

    id = Column(UUID(as_uuid=True), primary_key=True)
    machine_id = Column(String(32), ForeignKey("machines.id"), nullable=False)
    site_id = Column(String(32), nullable=False)
    operator_id = Column(String(64), nullable=True)
    task_id = Column(String(64), nullable=True)
    category = Column(String(64), nullable=False)  # event type of the highest-severity entry
    severity = Column(String(16), nullable=False)
    escalated = Column(Boolean, nullable=False, default=False)
    status = Column(String(16), nullable=False, default="OPEN")  # OPEN | ACKNOWLEDGED | CLOSED
    opened_at = Column(DateTime(timezone=True), nullable=False)
    last_event_at = Column(DateTime(timezone=True), nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by = Column(UUID(as_uuid=True), nullable=True)
    close_note = Column(String(500), nullable=True)
    trigger_event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    event_count = Column(Integer, nullable=False, default=0)
    risk_level_at_open = Column(String(16), nullable=True)
    explanation_status = Column(String(16), nullable=False, default="PENDING")  # PENDING|READY|FALLBACK|FAILED
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_incidents_machine_status_last_event", "machine_id", "status", "last_event_at"),
        Index("ix_incidents_site_opened", "site_id", "opened_at"),
    )


class IncidentEvent(Base):
    """Links an Event to the Incident it was correlated into. event_id is
    UNIQUE: one event belongs to at most one incident (no double-counting)."""
    __tablename__ = "incident_events"

    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), primary_key=True, nullable=False)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), primary_key=True, nullable=False, unique=True)
    linked_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class IncidentTimeline(Base):
    """
    Ordered, bounded-growth timeline for an incident. Repeated events with
    the same entry_key within a short window merge into one row (count++)
    instead of one row per event — hot-path events can fire every frame.
    """
    __tablename__ = "incident_timeline"

    id = Column(Integer, Identity(start=1, cycle=False), primary_key=True)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False)
    kind = Column(String(16), nullable=False)  # EVENT | STATUS | EXPLANATION
    entry_key = Column(String(128), nullable=False)
    event_type = Column(String(64), nullable=True)
    severity = Column(String(16), nullable=True)
    first_ts = Column(DateTime(timezone=True), nullable=False)
    last_ts = Column(DateTime(timezone=True), nullable=False)
    count = Column(Integer, nullable=False, default=1)
    representative_event_id = Column(UUID(as_uuid=True), nullable=True)
    summary = Column(String(500), nullable=False)
    actor_id = Column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("incident_id", "entry_key", "first_ts", name="uq_incident_timeline_key_first_ts"),
        Index("ix_incident_timeline_incident_order", "incident_id", "first_ts", "id"),
    )


class IncidentExplanationRow(Base):
    """
    One row per successfully-generated (or fallen-back-to) incident
    explanation. Unique (incident_id, packet_hash) makes reprocessing the
    same incident state idempotent — the cold worker checks for an
    existing row before calling the LLM at all (Stage 3 Batch 3H).
    """
    __tablename__ = "incident_explanations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False)
    packet_hash = Column(String(64), nullable=False)
    source = Column(String(16), nullable=False)  # GROQ | FALLBACK
    model_name = Column(String(64), nullable=True)
    prompt_version = Column(String(32), nullable=False)
    summary = Column(String(600), nullable=False)
    probable_causes = Column(JSONB, nullable=False)
    recommended_actions = Column(JSONB, nullable=False)
    lesson = Column(JSONB, nullable=False)
    training_refs = Column(JSONB, nullable=False)
    confidence = Column(String(8), nullable=False)
    grounding = Column(JSONB, nullable=False)  # {valid, violations[], attempts}
    fallback_reason = Column(String(32), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("incident_id", "packet_hash", name="uq_incident_explanations_incident_packet"),
        Index("ix_incident_explanations_incident_created", "incident_id", "created_at"),
    )


class LessonRow(Base):
    """
    Stage 4A — one lesson per incident (deterministic id, unique incident_id:
    regenerating for the same incident is a no-op, matching the idempotency
    pattern of every other cold-path table). Delivered once via the hot
    worker's IDLE_HUB transition push (`delivered_at`), read/fetched at most
    once meaningfully by the operator's lesson player (`read_at`).
    """
    __tablename__ = "lessons"

    id = Column(UUID(as_uuid=True), primary_key=True)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False, unique=True)
    machine_id = Column(String(32), nullable=False)
    operator_id = Column(String(64), nullable=True)
    title = Column(String(80), nullable=False)
    short_tip = Column(String(200), nullable=False)
    explanation = Column(String(800), nullable=False)
    knowledge_refs = Column(JSONB, nullable=False)
    source = Column(String(16), nullable=False)  # GROQ | FALLBACK
    status = Column(String(16), nullable=False)  # READY | FALLBACK | FAILED
    fallback_reason = Column(String(32), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_lessons_operator_delivered", "operator_id", "delivered_at"),
    )


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
