from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_candidate
from ..models import Candidate, JobApplication, JobPosting, PipelineStage, Tenant
from ..schemas import (ApplicationOut, JobFacets, JobSearchOut, PublicJobOut)
from ..services.resume import match_percent, missing_skills

router = APIRouter(prefix="/api/candidate", tags=["candidate-jobs"])


def _public(job: JobPosting, tenant: Tenant, *, applied: bool,
            cand_skills: list[str]) -> PublicJobOut:
    req = job.required_skills_json or []
    have = {s.lower() for s in cand_skills}
    return PublicJobOut(
        id=job.id, title=job.title, department=job.department, location=job.location,
        country=job.country, remote_mode=job.remote_mode,
        employment_type=job.employment_type, jd_text=job.jd_text,
        required_skills_json=req, company_name=tenant.name,
        published_at=job.published_at,
        min_experience_years=job.min_experience_years,
        max_experience_years=job.max_experience_years,
        stage_names=[st.name for st in job.stages],
        stages=[{"seq": st.seq, "name": st.name, "kind": st.kind} for st in job.stages],
        already_applied=applied,
        match_pct=match_percent(cand_skills, req),
        matched_skills=[x for x in req if x.lower() in have],
        missing_skills=missing_skills(cand_skills, req),
    )


@router.get("/jobs", response_model=JobSearchOut)
def browse_jobs(
    q: str | None = Query(default=None, description="Free text over title, JD and company"),
    country: str | None = None,
    employment_type: str | None = None,
    remote_mode: str | None = None,
    skills: str | None = Query(default=None, description="Comma-separated skill names"),
    min_experience: int | None = Query(default=None, ge=0, le=50),
    max_experience: int | None = Query(default=None, ge=0, le=50),
    match_only: bool = Query(default=False, description="Only roles the profile matches at all"),
    sort: str = Query(default="recent", pattern="^(recent|match|experience)$"),
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> JobSearchOut:
    """Filtered job search with resume-derived match scoring.

    Every filter narrows an *open* posting set. Facets are computed from that same set
    rather than hardcoded, so the UI can never offer a filter that returns nothing.
    """
    stmt = (
        select(JobPosting, Tenant)
        .join(Tenant, Tenant.id == JobPosting.tenant_id)
        .where(JobPosting.status == "open")
    )
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(or_(JobPosting.title.ilike(needle),
                              JobPosting.jd_text.ilike(needle),
                              Tenant.name.ilike(needle)))
    if country:
        stmt = stmt.where(JobPosting.country == country)
    if employment_type:
        stmt = stmt.where(JobPosting.employment_type == employment_type)
    if remote_mode:
        stmt = stmt.where(JobPosting.remote_mode == remote_mode)

    # Experience is a RANGE overlap, not a point test. A candidate with 6 years should
    # see a 3-8 role; a NULL bound means the employer left it open, so it always matches.
    if min_experience is not None:
        stmt = stmt.where(or_(JobPosting.max_experience_years.is_(None),
                              JobPosting.max_experience_years >= min_experience))
    if max_experience is not None:
        stmt = stmt.where(or_(JobPosting.min_experience_years.is_(None),
                              JobPosting.min_experience_years <= max_experience))

    rows = db.execute(stmt).all()

    applied_ids = set(db.scalars(
        select(JobApplication.job_id).where(JobApplication.candidate_id == cand.id)).all())

    sections = cand.profile_sections_json or {}
    cand_skills: list[str] = list(sections.get("skills") or [])

    wanted = [s.strip().lower() for s in (skills or "").split(",") if s.strip()]

    out: list[PublicJobOut] = []
    for job, tenant in rows:
        if wanted:
            req_lower = {x.lower() for x in (job.required_skills_json or [])}
            if not any(w in req_lower for w in wanted):
                continue
        item = _public(job, tenant, applied=job.id in applied_ids, cand_skills=cand_skills)
        if match_only and not item.match_pct:
            continue
        out.append(item)

    if sort == "match":
        # Unscored roles sort last rather than as 0% — see match_percent().
        out.sort(key=lambda j: (j.match_pct is None, -(j.match_pct or 0)))
    elif sort == "experience":
        out.sort(key=lambda j: (j.min_experience_years is None, j.min_experience_years or 0))
    else:
        out.sort(key=lambda j: j.published_at or j.id, reverse=True)

    # Facets from the unfiltered open set, so the panel stays stable while filtering.
    all_open = db.execute(
        select(JobPosting, Tenant)
        .join(Tenant, Tenant.id == JobPosting.tenant_id)
        .where(JobPosting.status == "open")
    ).all()
    facet_skills: list[str] = []
    for job, _ in all_open:
        for sk in (job.required_skills_json or []):
            if sk not in facet_skills:
                facet_skills.append(sk)

    facets = JobFacets(
        countries=sorted({j.country for j, _ in all_open if j.country}),
        employment_types=sorted({j.employment_type for j, _ in all_open if j.employment_type}),
        remote_modes=sorted({j.remote_mode for j, _ in all_open if j.remote_mode}),
        skills=sorted(facet_skills),
        total_open=len(all_open),
    )

    return JobSearchOut(jobs=out, facets=facets, profile_skills=cand_skills,
                        profile_years=cand.years_experience)


@router.get("/jobs/{job_id}", response_model=PublicJobOut)
def job_detail(
    job_id: int,
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> PublicJobOut:
    row = db.execute(
        select(JobPosting, Tenant)
        .join(Tenant, Tenant.id == JobPosting.tenant_id)
        .where(JobPosting.id == job_id, JobPosting.status == "open")
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This role is no longer open")

    job, tenant = row
    applied = db.scalar(select(JobApplication.id).where(
        JobApplication.job_id == job.id, JobApplication.candidate_id == cand.id))
    sections = cand.profile_sections_json or {}
    return _public(job, tenant, applied=applied is not None,
                   cand_skills=list(sections.get("skills") or []))


@router.post("/apply/{job_id}", response_model=ApplicationOut, status_code=201)
def apply(
    job_id: int,
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> ApplicationOut:
    job = db.get(JobPosting, job_id)
    if job is None or job.status != "open":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This role is no longer open")

    duplicate = db.scalar(
        select(JobApplication).where(
            JobApplication.job_id == job.id, JobApplication.candidate_id == cand.id
        )
    )
    if duplicate:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already applied to this role")

    first_stage = db.scalar(
        select(PipelineStage)
        .where(PipelineStage.job_id == job.id)
        .order_by(PipelineStage.seq)
        .limit(1)
    )

    app = JobApplication(
        job_id=job.id,
        candidate_id=cand.id,
        tenant_id=job.tenant_id,
        current_stage_id=first_stage.id if first_stage else None,
        status="applied",
    )
    db.add(app)
    db.commit()
    db.refresh(app)

    tenant = db.get(Tenant, job.tenant_id)
    return ApplicationOut(
        id=app.id,
        job_id=job.id,
        job_title=job.title,
        company_name=tenant.name if tenant else "",
        status=app.status,
        current_stage_name=first_stage.name if first_stage else None,
        applied_at=app.applied_at,
        last_activity_at=app.last_activity_at,
    )


@router.get("/me/applications", response_model=list[ApplicationOut])
def my_applications(
    cand: Candidate = Depends(current_candidate), db: Session = Depends(get_db)
) -> list[ApplicationOut]:
    rows = db.execute(
        select(JobApplication, JobPosting, Tenant)
        .join(JobPosting, JobPosting.id == JobApplication.job_id)
        .join(Tenant, Tenant.id == JobApplication.tenant_id)
        .where(JobApplication.candidate_id == cand.id)
        .order_by(JobApplication.applied_at.desc())
    ).all()

    out: list[ApplicationOut] = []
    for app, job, tenant in rows:
        stage = db.get(PipelineStage, app.current_stage_id) if app.current_stage_id else None
        out.append(
            ApplicationOut(
                id=app.id,
                job_id=job.id,
                job_title=job.title,
                company_name=tenant.name,
                status=app.status,
                current_stage_name=stage.name if stage else None,
                applied_at=app.applied_at,
                last_activity_at=app.last_activity_at,
            )
        )
    return out
