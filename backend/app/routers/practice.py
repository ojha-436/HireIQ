"""Candidate PRACTICE mode (Phase 2/3/13/14).

Same InterviewRuntime, same moderator, same personas, same assessment engine as the
employer hiring flow in `interview.py` — that module's WebSocket, consent, and Agora-token
endpoints are already session-id-keyed and ownership-checked only, so a practice session
rides them completely unchanged. This module owns exactly the parts that differ:

  * session creation with no `JobApplication` (a candidate practises on their own)
  * a session-id-keyed report read (the employer/candidate report endpoints in
    `interview.py` are both keyed by `application_id`, which a practice session has none of)
  * progress across retakes, and a dashboard readiness score

`InterviewRuntime._build_practice_report` (session.py) is the other half: it skips every
employer side-effect (notifications, audit log, pipeline auto-advance) and attaches a
coaching plan, which never happens for a hiring session.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import current_candidate
from app.engines import datasets as ds
from app.interview import context as CTX
from app.interview import personas as P
from app.models import Candidate, InterviewAssessment, InterviewSession, InterviewTurn, JobPosting
from app.routers.interview import _adaptivity, _channel_for, _report_lines, _sections_from_profile

router = APIRouter(tags=["practice"])


# =========================================================================== schemas
class PracticeStart(BaseModel):
    # Practice "this job" (Phase 6: Discover -> Match -> Practice) — grounds the panel in
    # a real, published job's required skills without ever creating a JobApplication.
    job_id: Optional[int] = None
    # Or a candidate-chosen skill set, for practice with no specific job in mind. Names
    # must be canonical (services/skills.LEXICON) — unknown names are silently dropped
    # rather than rejected, since a stray skill should not block starting a session.
    skill_names: list[str] = Field(default_factory=list)
    preset: Optional[str] = None
    panel: Optional[list[str]] = None
    # Phase 11: candidate/self-configured emphasis. Unknown dimensions are dropped;
    # missing dimensions default to weight 1.0 (see assessment.compute_overall).
    weights: dict[str, float] = Field(default_factory=dict)


def _clean_weights(raw: dict[str, float]) -> dict[str, float]:
    return {k: max(0.0, float(v)) for k, v in (raw or {}).items()
            if k in P.RUBRIC_DIMENSIONS and isinstance(v, (int, float))}


def _session_row(db: Session, session_id: str, cand: Candidate) -> InterviewSession:
    s = db.get(InterviewSession, session_id)
    if s is None or s.candidate_id != cand.id or s.session_type != "practice":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Practice interview not found")
    return s


# =========================================================================== start
@router.post("/api/candidate/practice/start", status_code=201)
def start_practice(
    body: PracticeStart,
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    job_title = ""
    required_skill_ids: list[str] = []

    if body.job_id is not None:
        job = db.get(JobPosting, body.job_id)
        if job is None or job.status not in ("open", "paused", "closed"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        job_title = job.title
        required_skill_ids = ds.to_ids(job.required_skills_json or [])
    elif body.skill_names:
        required_skill_ids = ds.to_ids(body.skill_names)
    else:
        # No target chosen: fall back to the candidate's own profile skills, so a
        # first-time "just start practising" click still grounds on something real.
        profile_skills = (cand.profile_sections_json or {}).get("skills") or []
        required_skill_ids = ds.to_ids(list(profile_skills))

    preset = body.preset if body.preset in P.PRESETS else settings.INTERVIEW_PRESET
    if preset not in P.PRESETS:
        preset = "panel"

    panel = [k for k in (body.panel or []) if k in P.PERSONAS]
    if not panel:
        panel = P.propose_panel(required_skill_ids, job_title)
    panel = panel[:P.PRESETS[preset]["panel_size"]] or ["tech"]

    weights = _clean_weights(body.weights)

    grounding = CTX.build(
        job={
            "job_title": job_title,
            "company": "",
            "required_skill_ids": required_skill_ids,
            "sector": "",
        },
        sections=_sections_from_profile(cand),
    )

    sess = InterviewSession(
        job_application_id=None,
        application_id=None,
        pipeline_stage_id=None,
        candidate_id=cand.id,
        initiated_by=None,
        session_type="practice",
        preset=preset,
        panel_json=panel,
        status="setup",
        state_json={
            "grounding": grounding,
            "practice": {"job_id": body.job_id, "job_title": job_title, "weights": weights},
        },
    )
    db.add(sess)
    db.flush()
    sess.agora_channel = _channel_for(sess.id)
    db.commit()
    db.refresh(sess)

    return {
        "session_id": sess.id,
        "agora_channel": sess.agora_channel,
        "panel": [{"key": k, "label": P.get(k).label} for k in panel],
        "job_title": job_title,
        "minutes": P.PRESETS[preset]["minutes"],
        "status": sess.status,
    }


# =========================================================================== list / retake
@router.get("/api/candidate/practice/sessions")
def list_practice_sessions(
    cand: Candidate = Depends(current_candidate), db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(InterviewSession)
        .where(InterviewSession.candidate_id == cand.id, InterviewSession.session_type == "practice")
        .order_by(InterviewSession.created_at.desc())
    ).all()

    out = []
    for s in rows:
        practice_cfg = (s.state_json or {}).get("practice") or {}
        row = db.scalar(select(InterviewAssessment).where(InterviewAssessment.session_id == s.id))
        out.append({
            "session_id": s.id,
            "job_id": practice_cfg.get("job_id"),
            "job_title": practice_cfg.get("job_title") or "",
            "status": s.status,
            "created_at": s.created_at,
            "ended_at": s.ended_at,
            "disclosure_accepted": s.disclosure_accepted_at is not None,
            "panel": [{"key": k, "label": P.get(k).label} for k in (s.panel_json or []) if k in P.PERSONAS],
            "overall": row.overall if row else None,
            "recommendation": row.recommendation if row else None,
        })
    return out


# =========================================================================== report
@router.get("/api/candidate/practice/sessions/{session_id}/report")
def practice_report(
    session_id: str,
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sess = _session_row(db, session_id, cand)
    row = db.scalar(select(InterviewAssessment).where(InterviewAssessment.session_id == sess.id))
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This practice interview has not produced a report yet.")

    report = row.report_json or {}
    turns = db.scalars(
        select(InterviewTurn).where(InterviewTurn.session_id == sess.id).order_by(InterviewTurn.seq)
    ).all()
    practice_cfg = (sess.state_json or {}).get("practice") or {}

    return {
        "session_id": sess.id,
        "job_id": practice_cfg.get("job_id"),
        "job_title": practice_cfg.get("job_title") or "",
        "status": sess.status,
        "overall": row.overall,
        "recommendation": row.recommendation,
        "summary": report.get("summary", ""),
        "dimensions": report.get("per_dimension") or [],
        "strengths": report.get("strengths") or [],
        "focus_areas": report.get("focus_areas") or [],
        "contradictions": report.get("contradictions") or [],
        "coverage": report.get("coverage") or {},
        "per_skill": row.per_skill_json or {},
        "difficulty_trajectory": row.difficulty_trajectory_json or [],
        "weights_applied": report.get("weights_applied") or {},
        "ai_disclosure": report.get("ai_disclosure", ""),
        "evidence": _report_lines(report),
        "adaptivity": _adaptivity(list(turns)),
        # Phase 13 — present only for practice sessions; never for a hiring assessment.
        "coaching": report.get("coaching"),
    }


# =========================================================================== progress
@router.get("/api/candidate/practice/progress")
def practice_progress(
    cand: Candidate = Depends(current_candidate), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Score trend across every retake — the closed-loop-learning view (Phase 14)."""
    sessions = db.scalars(
        select(InterviewSession)
        .where(InterviewSession.candidate_id == cand.id, InterviewSession.session_type == "practice",
               InterviewSession.status == "ended")
        .order_by(InterviewSession.created_at.asc())
    ).all()

    history = []
    for s in sessions:
        row = db.scalar(select(InterviewAssessment).where(InterviewAssessment.session_id == s.id))
        if row is None:
            continue
        practice_cfg = (s.state_json or {}).get("practice") or {}
        dims = {d["dimension"]: d["score"] for d in (row.report_json or {}).get("per_dimension", [])}
        history.append({
            "session_id": s.id,
            "created_at": s.created_at,
            "job_title": practice_cfg.get("job_title") or "",
            "overall": row.overall,
            "dimensions": dims,
        })

    trend: dict[str, list[float]] = {}
    for entry in history:
        for dim, score in entry["dimensions"].items():
            trend.setdefault(dim, []).append(score)

    return {"attempts": len(history), "history": history, "dimension_trend": trend}


# =========================================================================== dashboard
@router.get("/api/candidate/dashboard")
def candidate_dashboard(
    cand: Candidate = Depends(current_candidate), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Interview Readiness score (Phase 3) — built only from the candidate's own history.

    Never fabricated: with no completed practice interview yet, `readiness` is null and
    the recommendation is simply to take one. This deliberately mirrors the product's
    "no fabricated percentile" rule for hiring assessments (ARCHITECTURE.md §9).
    """
    recent = db.scalars(
        select(InterviewSession)
        .where(InterviewSession.candidate_id == cand.id, InterviewSession.session_type == "practice",
               InterviewSession.status == "ended")
        .order_by(InterviewSession.created_at.desc())
        .limit(3)
    ).all()

    dim_totals: dict[str, list[float]] = {}
    recent_out = []
    for s in recent:
        row = db.scalar(select(InterviewAssessment).where(InterviewAssessment.session_id == s.id))
        if row is None:
            continue
        practice_cfg = (s.state_json or {}).get("practice") or {}
        recent_out.append({
            "session_id": s.id, "job_title": practice_cfg.get("job_title") or "",
            "overall": row.overall, "created_at": s.created_at,
        })
        for d in (row.report_json or {}).get("per_dimension", []):
            dim_totals.setdefault(d["dimension"], []).append(d["score"])

    dimension_avg = {dim: round(sum(v) / len(v), 1) for dim, v in dim_totals.items()}
    readiness = round(sum(dimension_avg.values()) / len(dimension_avg) * 20) if dimension_avg else None

    recommended_action = "Complete a practice interview to see your Interview Readiness score."
    weakest = None
    if dimension_avg:
        weakest = min(dimension_avg, key=dimension_avg.get)
        recommended_action = "Improve {}".format(weakest.replace("_", " ").title())

    total_practice = db.scalar(
        select(InterviewSession.id)
        .where(InterviewSession.candidate_id == cand.id, InterviewSession.session_type == "practice",
               InterviewSession.status == "ended")
    )

    return {
        "readiness": readiness,
        "dimensions": dimension_avg,
        "weakest_dimension": weakest,
        "recommended_action": recommended_action,
        "recent_practice_sessions": recent_out,
        "has_practice_history": bool(total_practice),
    }
