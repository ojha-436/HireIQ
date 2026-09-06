from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..config import get_settings
from ..db import get_db
from ..deps import current_candidate
from ..models import Candidate
from ..schemas import (
    CandidateMe,
    CandidateProfileUpdate,
    CandidateRegister,
    LoginRequest,
    PasswordChange,
    ResumeParsed,
    TokenResponse,
)
from ..services.profile_merge import (apply_manual_edit, merge_resume,
                                      public_sections)
from ..services.resume import ResumeError, parse_resume
from ..security import hash_password, mint_token, verify_password

router = APIRouter(prefix="/api/candidate/auth", tags=["candidate-auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: CandidateRegister, db: Session = Depends(get_db)) -> TokenResponse:
    if db.scalar(select(Candidate).where(Candidate.email == body.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")

    cand = Candidate(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        phone=body.phone,
        profile_sections_json={"headline": "", "summary": "", "skills": [], "experience": []},
    )
    db.add(cand)
    db.commit()

    return TokenResponse(
        token=mint_token(cand.id, "candidate"),
        role="candidate",
        expires_in=get_settings().jwt_ttl_hours * 3600,
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    cand = db.scalar(select(Candidate).where(Candidate.email == body.email.lower()))
    if cand is None or not verify_password(body.password, cand.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")
    if not cand.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "This account has been suspended. Contact support.")

    return TokenResponse(
        token=mint_token(cand.id, "candidate"),
        role="candidate",
        expires_in=get_settings().jwt_ttl_hours * 3600,
    )


def _me(cand: Candidate) -> CandidateMe:
    return CandidateMe(
        id=cand.id,
        email=cand.email,
        full_name=cand.full_name,
        phone=cand.phone,
        country=cand.country,
        years_experience=cand.years_experience,
        profile_sections_json=public_sections(cand.profile_sections_json or {}),
        resume_meta_json=cand.resume_meta_json or {},
    )


@router.get("/me", response_model=CandidateMe)
def me(cand: Candidate = Depends(current_candidate)) -> CandidateMe:
    return _me(cand)


@router.patch("/me", response_model=CandidateMe)
def update_me(
    body: CandidateProfileUpdate,
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> CandidateMe:
    if body.full_name is not None:
        cand.full_name = body.full_name
    if body.phone is not None:
        cand.phone = body.phone
    if body.country is not None:
        cand.country = body.country
    if body.years_experience is not None:
        cand.years_experience = body.years_experience
    if body.profile_sections_json is not None:
        # Not a straight assignment: the UI does not know about provenance, so a plain
        # save would drop it and the next résumé upload would believe it owned nothing.
        cand.profile_sections_json = apply_manual_edit(
            cand.profile_sections_json or {}, body.profile_sections_json)
    db.commit()
    db.refresh(cand)
    return _me(cand)


def _dedup_key(item: dict, *fields: str) -> tuple[str, ...]:
    return tuple(str(item.get(f, "") or "").strip().lower() for f in fields)


@router.post("/me/resume", response_model=ResumeParsed)
async def upload_resume(
    file: UploadFile = File(...),
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> ResumeParsed:
    """Parse a résumé into a profile draft and fold it into the candidate's profile.

    The extracted text is NOT stored — only what is derived from it.

    A re-upload REPLACES what the previous résumé contributed and leaves anything the
    candidate typed by hand alone (see services/profile_merge). The earlier additive
    merge meant a replacement CV changed nothing that already had a value, so the
    profile kept a stale headline and accumulated two careers' worth of skills — which
    also froze every job match score, since those are computed from profile skills.
    """
    blob = await file.read()
    try:
        parsed = parse_resume(file.filename or "resume", blob)
    except ResumeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    sections, report = merge_resume(
        cand.profile_sections_json or {},
        parsed.get("profile") or {},
        parsed["skills"],
        filename=parsed["filename"],
        prior_resume_skills=(cand.resume_meta_json or {}).get("skills") or [],
    )

    cand.profile_sections_json = sections
    if parsed["years"] is not None:
        cand.years_experience = parsed["years"]
    cand.resume_meta_json = {
        "filename": parsed["filename"],
        "chars": parsed["chars"],
        "skills": parsed["skills"],
        "years": parsed["years"],
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    # SQLAlchemy does not see in-place mutation of a JSON column; both are reassigned
    # above, but the profile dict is derived from the old one and can share identity.
    flag_modified(cand, "profile_sections_json")
    db.commit()

    return ResumeParsed(
        filename=parsed["filename"], skills=parsed["skills"],
        years_experience=parsed["years"], chars=parsed["chars"],
        headline=sections.get("headline") or "",
        summary_drafted="summary" in report["filled"],
        experience_added=report["added"]["experience"],
        education_added=report["added"]["education"],
        projects_added=report["added"]["projects"],
        replaced_fields=report["replaced"],
        kept_manual_fields=report["kept_manual"],
        entries_replaced=sum(report["removed"].values()),
        skills_unreadable=report["skills_unreadable"],
    )


@router.patch("/me/password")
def change_password(
    body: PasswordChange,
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    if not verify_password(body.current_password, cand.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    cand.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
