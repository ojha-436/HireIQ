"""ORM models.

Phase 1 builds and uses the employer/candidate/job tables. The interview-engine tables are
declared now so migrations don't churn in Phase 3 — the schema is settled in ARCHITECTURE.md §4.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    """Interview rows use opaque UUID string ids.

    Two reasons, both load-bearing: the ported engine threads `session_id: str`
    through every layer, and a session id appears in candidate-facing URLs — a
    sequential integer there would let anyone enumerate other people's interviews.
    """
    return uuid.uuid4().hex


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


# ============================================================ employer side (tenant-scoped)
class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(40), default="trial")
    industry: Mapped[str | None] = mapped_column(String(120))
    size_band: Mapped[str | None] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    users: Mapped[list["TenantUser"]] = relationship(back_populates="tenant")
    jobs: Mapped[list["JobPosting"]] = relationship(back_populates="tenant")


class TenantUser(TimestampMixin, Base):
    __tablename__ = "tenant_users"
    __table_args__ = (UniqueConstraint("email", name="uq_tenant_user_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="admin")  # admin|recruiter|hiring_manager
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class JobPosting(TimestampMixin, Base):
    __tablename__ = "job_postings"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    posted_by: Mapped[int] = mapped_column(ForeignKey("tenant_users.id"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str | None] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(160))
    employment_type: Mapped[str] = mapped_column(String(40), default="full_time")
    jd_text: Mapped[str] = mapped_column(Text, default="")
    required_skills_json: Mapped[list] = mapped_column(JSON, default=list)
    country: Mapped[str | None] = mapped_column(String(80), index=True)
    remote_mode: Mapped[str] = mapped_column(String(20), default="onsite")  # onsite|hybrid|remote
    min_experience_years: Mapped[int | None] = mapped_column(Integer, index=True)
    max_experience_years: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft|open|paused|closed
    blind_screening_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closes_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    tenant: Mapped[Tenant] = relationship(back_populates="jobs")
    stages: Mapped[list["PipelineStage"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="PipelineStage.seq"
    )
    applications: Mapped[list["JobApplication"]] = relationship(back_populates="job")


class PipelineStage(TimestampMixin, Base):
    __tablename__ = "pipeline_stages"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="ai_interview")
    # {panel: [...], preset, start_difficulty, target_skills: [...]}
    interview_config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Score at or above which the engine may recommend auto-advance. NULL = always human.
    auto_advance_threshold: Mapped[int | None] = mapped_column(Integer)

    job: Mapped[JobPosting] = relationship(back_populates="stages")


class AuditLog(TimestampMixin, Base):
    """Append-only. Never updated, never deleted — see plan.md §compliance."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("tenant_users.id"))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)


# ============================================================ candidate side (global pool)
class Candidate(TimestampMixin, Base):
    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("email", name="uq_candidate_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    country: Mapped[str | None] = mapped_column(String(80))
    years_experience: Mapped[float | None] = mapped_column()
    # {headline, summary, experience: [...], skills: [...], education: [...]}
    profile_sections_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Parsed resume: {filename, uploaded_at, chars, skills: [...], years: n}
    resume_meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Admin-managed suspension. A suspended candidate keeps their data but cannot log in.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class JobApplication(TimestampMixin, Base):
    __tablename__ = "job_applications"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_application"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)  # denormalised
    current_stage_id: Mapped[int | None] = mapped_column(ForeignKey("pipeline_stages.id"))
    status: Mapped[str] = mapped_column(String(30), default="applied", index=True)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    identity_revealed: Mapped[bool] = mapped_column(Boolean, default=False)

    job: Mapped[JobPosting] = relationship(back_populates="applications")
    candidate: Mapped[Candidate] = relationship()
    current_stage: Mapped[PipelineStage | None] = relationship()


# ============================================================ interview engine (Phase 3+)
class InterviewSession(TimestampMixin, Base):
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_application_id: Mapped[int | None] = mapped_column(ForeignKey("job_applications.id"), index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("job_applications.id"))
    pipeline_stage_id: Mapped[int | None] = mapped_column(ForeignKey("pipeline_stages.id"))
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"), index=True)
    initiated_by: Mapped[int | None] = mapped_column(ForeignKey("tenant_users.id"))

    session_type: Mapped[str] = mapped_column(String(20), default="standard")
    panel_json: Mapped[list] = mapped_column(JSON, default=list)
    preset: Mapped[str] = mapped_column(String(30), default="panel")
    agora_channel: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="setup", index=True)

    # Legally required: the session cannot go live while this is NULL.
    disclosure_accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    state_json: Mapped[dict] = mapped_column(JSON, default=dict)  # serialised CandidateContext
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_s: Mapped[int] = mapped_column(Integer, default=0)

    # Agora Cloud Recording (deferred; nullable so sessions without it are unaffected)
    recording_gcs_uri: Mapped[str | None] = mapped_column(String(400))
    recording_agora_sid: Mapped[str | None] = mapped_column(String(120))
    recording_agora_resource_id: Mapped[str | None] = mapped_column(String(200))
    recording_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    turns: Mapped[list["InterviewTurn"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="InterviewTurn.seq")
    assessment: Mapped["InterviewAssessment | None"] = relationship(
        back_populates="session", uselist=False, cascade="all, delete-orphan")


class InterviewTurn(Base):
    """60-day TTL. Cited quotes are snapshotted into the assessment before purge."""

    __tablename__ = "interview_turns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    speaker: Mapped[str] = mapped_column(String(30))  # 'candidate' | persona key
    text: Mapped[str] = mapped_column(Text, default="")
    started_ms: Mapped[int] = mapped_column(Integer, default=0)
    ended_ms: Mapped[int] = mapped_column(Integer, default=0)
    analysis_json: Mapped[dict | None] = mapped_column(JSON)
    flags_json: Mapped[list] = mapped_column(JSON, default=list)

    # HireIQ additions — drive the trace rail and the difficulty trajectory chart.
    rule_fired: Mapped[str | None] = mapped_column(String(16))
    question_source: Mapped[str | None] = mapped_column(String(20))
    difficulty_at_turn: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    session: Mapped[InterviewSession] = relationship(back_populates="turns")


class InterviewAssessment(TimestampMixin, Base):
    """Kept forever. `overall` is arithmetic Python — never model-produced."""

    __tablename__ = "interview_assessments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id"), index=True)
    overall: Mapped[int] = mapped_column(Integer, default=0)
    recommendation: Mapped[str] = mapped_column(String(40), default="lean_no")
    source: Mapped[str] = mapped_column(String(10), default="ai")  # ai | human
    percentile: Mapped[int | None] = mapped_column(Integer)
    percentile_n: Mapped[int] = mapped_column(Integer, default=0)
    report_json: Mapped[dict] = mapped_column(JSON, default=dict)
    per_skill_json: Mapped[dict] = mapped_column(JSON, default=dict)
    difficulty_trajectory_json: Mapped[list] = mapped_column(JSON, default=list)
    released_to_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    session: Mapped[InterviewSession] = relationship(back_populates="assessment")


class InterviewQuestion(Base):
    """Seed bank — fallback only. Dynamic generation is the default path."""

    __tablename__ = "interview_questions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    skill_id: Mapped[str] = mapped_column(String(80), index=True)
    persona: Mapped[str] = mapped_column(String(40), index=True)
    difficulty: Mapped[int] = mapped_column(Integer, default=3, index=True)
    kind: Mapped[str] = mapped_column(String(40), default="probe")
    question: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(120))


class Scenario(Base):
    """Role-play engine (PS11 #6). Escalations keyed by difficulty level."""

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    role_family: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    persona_owner: Mapped[str] = mapped_column(String(40))
    difficulty_floor: Mapped[int] = mapped_column(Integer, default=3)
    setup_text: Mapped[str] = mapped_column(Text)
    injects_json: Mapped[list] = mapped_column(JSON, default=list)
    escalations_json: Mapped[dict] = mapped_column(JSON, default=dict)
    success_signals_json: Mapped[list] = mapped_column(JSON, default=list)
    skills_json: Mapped[list] = mapped_column(JSON, default=list)


# --------------------------------------------------------------- engine compatibility
#: The ported interview engine refers to the application row as `Application`.
Application = JobApplication


class CalibrationBaseline(TimestampMixin, Base):
    """Anchors a job's scoring to the employer's own standard (plan.md §5, deferred UI)."""

    __tablename__ = "calibration_baselines"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id"), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("interview_sessions.id"))
    skill_anchors_json: Mapped[dict] = mapped_column(JSON, default=dict)
    overall_anchor: Mapped[float | None] = mapped_column()
    created_by: Mapped[int | None] = mapped_column(ForeignKey("tenant_users.id"))


class Notification(TimestampMixin, Base):
    """In-app notifications. Email transport is deferred; see app/notify.py."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_type: Mapped[str] = mapped_column(String(20))
    recipient_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(60))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ============================================================ platform admin
class AdminUser(TimestampMixin, Base):
    """Platform operator. Separate audience from employer/candidate — see security.py.

    Seeded once at startup (username 'admin', password 'admin@123') if the table is
    empty; changeable via POST /api/admin/auth/me/password. Not self-registerable.
    """

    __tablename__ = "admin_users"
    __table_args__ = (UniqueConstraint("username", name="uq_admin_username"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
