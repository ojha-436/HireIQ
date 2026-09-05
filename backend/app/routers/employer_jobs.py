from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_employer
from ..models import AuditLog, Candidate, JobApplication, JobPosting, PipelineStage, TenantUser
from ..schemas import (
    ApplicantOut,
    JobCreate,
    JobDescriptionGenerate,
    JobOut,
    JobUpdate,
    PipelineReplace,
    StageOut,
)
from ..services import jd_generator
from ..services.skills import default_pipeline, extract_experience_range, extract_skills

router = APIRouter(prefix="/api/employer/jobs", tags=["employer-jobs"])


@router.post("/generate-description")
def generate_description(
    body: JobDescriptionGenerate,
    user: TenantUser = Depends(current_employer),
) -> dict[str, str]:
    """Draft a job description from a title (+ optional department/seniority/keywords).

    Uses Gemini when configured, otherwise a deterministic template — either way the
    employer gets a full, editable draft back. Nothing is saved by this call; the
    employer reviews and edits before it ever reaches `POST /` or `PATCH /{job_id}`.
    """
    return jd_generator.generate(
        title=body.title, department=body.department,
        seniority=body.seniority, keywords=body.keywords,
    )


def _audit(db: Session, user: TenantUser, action: str, subject_id: int, payload: dict) -> None:
    db.add(
        AuditLog(
            tenant_id=user.tenant_id,
            actor_id=user.id,
            action=action,
            subject_type="job",
            subject_id=subject_id,
            payload_json=payload,
        )
    )


def _owned_job(db: Session, job_id: int, user: TenantUser) -> JobPosting:
    """Fetch a job or 404. Cross-tenant access is a 404, not a 403 — don't confirm existence."""
    job = db.get(JobPosting, job_id)
    if job is None or job.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


def _to_out(db: Session, job: JobPosting) -> JobOut:
    count = db.scalar(
        select(func.count(JobApplication.id)).where(JobApplication.job_id == job.id)
    )
    return JobOut(
        id=job.id,
        title=job.title,
        department=job.department,
        location=job.location,
        country=job.country,
        remote_mode=job.remote_mode,
        employment_type=job.employment_type,
        min_experience_years=job.min_experience_years,
        max_experience_years=job.max_experience_years,
        jd_text=job.jd_text,
        required_skills_json=job.required_skills_json or [],
        status=job.status,
        published_at=job.published_at,
        created_at=job.created_at,
        applicant_count=count or 0,
        stages=[
            StageOut(
                id=s.id,
                seq=s.seq,
                name=s.name,
                kind=s.kind,
                interview_config_json=s.interview_config_json or {},
            )
            for s in job.stages
        ],
    )


@router.post("/", response_model=JobOut, status_code=201)
def create_job(
    body: JobCreate,
    user: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> JobOut:
    skills = extract_skills(body.jd_text)
    lo, hi = extract_experience_range(body.jd_text)

    job = JobPosting(
        tenant_id=user.tenant_id,
        posted_by=user.id,
        title=body.title,
        department=body.department,
        location=body.location,
        country=body.country,
        remote_mode=body.remote_mode,
        employment_type=body.employment_type,
        jd_text=body.jd_text,
        required_skills_json=skills,
        # Explicit employer input wins over what was parsed from the JD prose.
        min_experience_years=body.min_experience_years if body.min_experience_years is not None else lo,
        max_experience_years=body.max_experience_years if body.max_experience_years is not None else hi,
        status="draft",
    )
    db.add(job)
    db.flush()

    # The employer may define the pipeline at creation; otherwise take the proposal.
    if body.stages:
        stages = [
            {"seq": st.seq, "name": st.name, "kind": st.kind,
             "interview_config_json": st.interview_config_json}
            for st in sorted(body.stages, key=lambda x: x.seq)
        ]
    else:
        stages = default_pipeline(skills)

    for stage in stages:
        db.add(PipelineStage(job_id=job.id, **stage))

    _audit(db, user, "job.create", job.id,
           {"title": job.title, "skills": skills,
            "stages": [(st["seq"], st["kind"]) for st in stages]})
    db.commit()
    db.refresh(job)
    return _to_out(db, job)


@router.get("/", response_model=list[JobOut])
def list_jobs(
    user: TenantUser = Depends(current_employer), db: Session = Depends(get_db)
) -> list[JobOut]:
    jobs = db.scalars(
        select(JobPosting)
        .where(JobPosting.tenant_id == user.tenant_id)
        .order_by(JobPosting.created_at.desc())
    ).all()
    return [_to_out(db, j) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: int,
    user: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> JobOut:
    return _to_out(db, _owned_job(db, job_id, user))


@router.patch("/{job_id}", response_model=JobOut)
def update_job(
    job_id: int,
    body: JobUpdate,
    user: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> JobOut:
    job = _owned_job(db, job_id, user)
    data = body.model_dump(exclude_unset=True)

    # Re-extract skills when the JD changes, unless the employer set them explicitly.
    if "jd_text" in data and "required_skills_json" not in data:
        data["required_skills_json"] = extract_skills(data["jd_text"])

    for field, value in data.items():
        setattr(job, field, value)

    _audit(db, user, "job.update", job.id, {"fields": list(data)})
    db.commit()
    db.refresh(job)
    return _to_out(db, job)


@router.post("/{job_id}/publish", response_model=JobOut)
def publish_job(
    job_id: int,
    user: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> JobOut:
    job = _owned_job(db, job_id, user)
    if not job.stages:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Add at least one pipeline stage before publishing this role",
        )
    job.status = "open"
    job.published_at = job.published_at or datetime.now(timezone.utc)
    _audit(db, user, "job.publish", job.id, {})
    db.commit()
    db.refresh(job)
    return _to_out(db, job)


@router.post("/{job_id}/pause", response_model=JobOut)
def pause_job(
    job_id: int,
    user: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> JobOut:
    job = _owned_job(db, job_id, user)
    job.status = "paused"
    _audit(db, user, "job.pause", job.id, {})
    db.commit()
    db.refresh(job)
    return _to_out(db, job)


@router.post("/{job_id}/close", response_model=JobOut)
def close_job(
    job_id: int,
    user: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> JobOut:
    job = _owned_job(db, job_id, user)
    job.status = "closed"
    _audit(db, user, "job.close", job.id, {})
    db.commit()
    db.refresh(job)
    return _to_out(db, job)


@router.put("/{job_id}/pipeline", response_model=JobOut)
def replace_pipeline(
    job_id: int,
    body: PipelineReplace,
    user: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> JobOut:
    job = _owned_job(db, job_id, user)
    if not body.stages:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A pipeline needs at least one stage")

    in_use = db.scalar(
        select(func.count(JobApplication.id)).where(
            JobApplication.job_id == job.id, JobApplication.current_stage_id.isnot(None)
        )
    )
    if in_use:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{in_use} candidate(s) are already in this pipeline. "
            "Editing stages now would orphan them — close the role or advance them first.",
        )

    for stage in list(job.stages):
        db.delete(stage)
    db.flush()

    for stage in sorted(body.stages, key=lambda s: s.seq):
        db.add(
            PipelineStage(
                job_id=job.id,
                seq=stage.seq,
                name=stage.name,
                kind=stage.kind,
                interview_config_json=stage.interview_config_json,
            )
        )

    _audit(db, user, "job.pipeline.replace", job.id, {"stage_count": len(body.stages)})
    db.commit()
    db.refresh(job)
    return _to_out(db, job)


@router.get("/{job_id}/applications", response_model=list[ApplicantOut])
def list_applications(
    job_id: int,
    user: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> list[ApplicantOut]:
    job = _owned_job(db, job_id, user)
    rows = db.execute(
        select(JobApplication, Candidate)
        .join(Candidate, Candidate.id == JobApplication.candidate_id)
        .where(JobApplication.job_id == job.id)
        .order_by(JobApplication.applied_at.desc())
    ).all()

    return [
        ApplicantOut(
            id=app.id,
            candidate_id=cand.id,
            full_name=cand.full_name,
            email=cand.email,
            headline=(cand.profile_sections_json or {}).get("headline"),
            status=app.status,
            current_stage_id=app.current_stage_id,
            applied_at=app.applied_at,
        )
        for app, cand in rows
    ]
