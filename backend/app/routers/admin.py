"""Platform admin: a third auth audience that manages both portals plus platform health.

Not self-registerable. `AdminUser` is seeded once at startup (see main.py's lifespan) with
username 'admin' / password 'admin@123', changeable via PATCH /api/admin/auth/me/password.
Every admin route requires `current_admin` (deps.py) — a candidate or employer token is
structurally the wrong signature for this audience (security.py's three-secret design).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings, settings
from ..db import get_db
from ..deps import current_admin
from ..interview import session as RT
from ..models import (AdminUser, Candidate, InterviewAssessment, InterviewSession,
                      JobApplication, JobPosting, Tenant, TenantUser)
from ..schemas import (AdminLoginRequest, AdminMe, CandidateAdminOut, EmployerAdminOut,
                       PasswordChange, TokenResponse)
from ..security import hash_password, mint_token, verify_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


# =========================================================================== auth
@router.post("/auth/login", response_model=TokenResponse)
def login(body: AdminLoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(AdminUser).where(AdminUser.username == body.username.strip().lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Username or password is incorrect")
    return TokenResponse(
        token=mint_token(user.id, "admin", {}),
        role="admin",
        expires_in=get_settings().jwt_ttl_hours * 3600,
    )


@router.get("/auth/me", response_model=AdminMe)
def me(admin: AdminUser = Depends(current_admin)) -> AdminMe:
    return AdminMe(id=admin.id, username=admin.username)


@router.patch("/auth/me/password")
def change_password(
    body: PasswordChange, admin: AdminUser = Depends(current_admin), db: Session = Depends(get_db),
) -> dict[str, bool]:
    if not verify_password(body.current_password, admin.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    admin.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}


# =========================================================================== employers
@router.get("/employers", response_model=list[EmployerAdminOut])
def list_employers(
    admin: AdminUser = Depends(current_admin), db: Session = Depends(get_db),
) -> list[EmployerAdminOut]:
    tenants = db.scalars(select(Tenant).order_by(Tenant.created_at.desc())).all()
    out = []
    for t in tenants:
        job_count = db.scalar(select(func.count(JobPosting.id)).where(JobPosting.tenant_id == t.id)) or 0
        user_count = db.scalar(select(func.count(TenantUser.id)).where(TenantUser.tenant_id == t.id)) or 0
        out.append(EmployerAdminOut(
            id=t.id, name=t.name, domain=t.domain, active=t.active, plan=t.plan,
            created_at=t.created_at, job_count=job_count, user_count=user_count,
        ))
    return out


@router.post("/employers/{tenant_id}/toggle-active")
def toggle_employer(
    tenant_id: int, admin: AdminUser = Depends(current_admin), db: Session = Depends(get_db),
) -> dict[str, Any]:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employer not found")
    tenant.active = not tenant.active
    db.commit()
    return {"id": tenant.id, "active": tenant.active}


# =========================================================================== candidates
@router.get("/candidates", response_model=list[CandidateAdminOut])
def list_candidates(
    admin: AdminUser = Depends(current_admin), db: Session = Depends(get_db),
) -> list[CandidateAdminOut]:
    cands = db.scalars(select(Candidate).order_by(Candidate.created_at.desc())).all()
    out = []
    for c in cands:
        app_count = db.scalar(
            select(func.count(JobApplication.id)).where(JobApplication.candidate_id == c.id)) or 0
        out.append(CandidateAdminOut(
            id=c.id, full_name=c.full_name, email=c.email, is_active=c.is_active,
            created_at=c.created_at, application_count=app_count,
        ))
    return out


@router.post("/candidates/{candidate_id}/toggle-active")
def toggle_candidate(
    candidate_id: int, admin: AdminUser = Depends(current_admin), db: Session = Depends(get_db),
) -> dict[str, Any]:
    cand = db.get(Candidate, candidate_id)
    if cand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    cand.is_active = not cand.is_active
    db.commit()
    return {"id": cand.id, "is_active": cand.is_active}


# =========================================================================== health / kpis
@router.get("/health")
def health(
    admin: AdminUser = Depends(current_admin), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Deeper than the public `/api/health` — gated, because it names what is and
    isn't configured. Every field here is a live check or a config presence check,
    never a guess."""
    db_ok = True
    try:
        db.execute(select(1))
    except Exception:  # noqa: BLE001
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "database": {"connected": db_ok, "engine": "sqlite" if settings.is_sqlite else "postgres"},
        "gemini_configured": bool(settings.gemini_api_key),
        "gemini_live_model": settings.gemini_live_model,
        "agora_rtc_configured": bool(settings.agora_app_id and settings.agora_app_certificate),
        "agora_convoai_configured": bool(settings.agora_customer_id and settings.agora_customer_secret),
        "live_interview_sessions": RT.live_count(),
        "turn_ttl_days": settings.turn_ttl_days,
        "checked_at": datetime.now(timezone.utc),
    }


@router.get("/kpis")
def kpis(
    admin: AdminUser = Depends(current_admin), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """General platform KPIs — every number is a real aggregate query, nothing modelled."""
    now = datetime.now(timezone.utc)
    since_24h, since_7d = now - timedelta(hours=24), now - timedelta(days=7)

    def count(stmt) -> int:
        return db.scalar(stmt) or 0

    total_sessions = count(select(func.count(InterviewSession.id)))
    practice_sessions = count(
        select(func.count(InterviewSession.id)).where(InterviewSession.session_type == "practice"))
    avg_overall = db.scalar(select(func.avg(InterviewAssessment.overall)))

    return {
        "employers": {
            "total": count(select(func.count(Tenant.id))),
            "active": count(select(func.count(Tenant.id)).where(Tenant.active.is_(True))),
            "signups_7d": count(select(func.count(Tenant.id)).where(Tenant.created_at >= since_7d)),
        },
        "candidates": {
            "total": count(select(func.count(Candidate.id))),
            "active": count(select(func.count(Candidate.id)).where(Candidate.is_active.is_(True))),
            "signups_7d": count(select(func.count(Candidate.id)).where(Candidate.created_at >= since_7d)),
        },
        "jobs": {
            "total": count(select(func.count(JobPosting.id))),
            "open": count(select(func.count(JobPosting.id)).where(JobPosting.status == "open")),
        },
        "applications": {"total": count(select(func.count(JobApplication.id)))},
        "interviews": {
            "total": total_sessions,
            "hiring": total_sessions - practice_sessions,
            "practice": practice_sessions,
            "live": count(select(func.count(InterviewSession.id)).where(InterviewSession.status == "live")),
            "ended": count(select(func.count(InterviewSession.id)).where(InterviewSession.status == "ended")),
            "last_24h": count(select(func.count(InterviewSession.id)).where(InterviewSession.created_at >= since_24h)),
            "last_7d": count(select(func.count(InterviewSession.id)).where(InterviewSession.created_at >= since_7d)),
        },
        "assessments": {
            "total": count(select(func.count(InterviewAssessment.id))),
            "average_overall": round(avg_overall, 1) if avg_overall is not None else None,
        },
        "generated_at": now,
    }
