from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

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
        profile_sections_json=cand.profile_sections_json or {},
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
        cand.profile_sections_json = body.profile_sections_json
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
    """Parse a resume into a full profile draft, and fold it into the candidate's profile.

    The extracted text is NOT stored — only what's derived from it. Skills merge as
    before. Headline/summary/experience/education/projects merge additively: anything
    the candidate already typed by hand always wins, so a re-upload can only ADD to a
    profile, never silently overwrite what someone wrote themselves.
    """
    blob = await file.read()
    try:
        parsed = parse_resume(file.filename or "resume", blob)
    except ResumeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    profile = parsed.get("profile") or {}
    sections = dict(cand.profile_sections_json or {})

    sections["skills"] = list(dict.fromkeys([*(sections.get("skills") or []), *parsed["skills"]]))

    summary_drafted = False
    if not str(sections.get("headline") or "").strip() and profile.get("headline"):
        sections["headline"] = profile["headline"]
    if not str(sections.get("summary") or "").strip() and profile.get("summary"):
        sections["summary"] = profile["summary"]
        summary_drafted = True

    experience = list(sections.get("experience") or [])
    seen = {_dedup_key(e, "title", "org") for e in experience}
    added_exp = 0
    for item in profile.get("experience") or []:
        key = _dedup_key(item, "title", "org")
        if key not in seen and key != ("", ""):
            experience.append(item)
            seen.add(key)
            added_exp += 1
    sections["experience"] = experience[:8]

    education = list(sections.get("education") or [])
    seen_edu = {_dedup_key(e, "degree", "org") for e in education}
    added_edu = 0
    for item in profile.get("education") or []:
        key = _dedup_key(item, "degree", "org")
        if key not in seen_edu and key != ("", ""):
            education.append(item)
            seen_edu.add(key)
            added_edu += 1
    sections["education"] = education[:5]

    projects = list(sections.get("projects") or [])
    seen_proj = {_dedup_key(p, "title") for p in projects}
    added_proj = 0
    for item in profile.get("projects") or []:
        key = _dedup_key(item, "title")
        if key not in seen_proj and key != ("",):
            projects.append(item)
            seen_proj.add(key)
            added_proj += 1
    sections["projects"] = projects[:6]

    cand.profile_sections_json = sections
    if parsed["years"] is not None:
        cand.years_experience = parsed["years"]
    cand.resume_meta_json = {
        "filename": parsed["filename"],
        "chars": parsed["chars"],
        "skills": parsed["skills"],
        "years": parsed["years"],
        "uploaded_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
    }
    db.commit()

    return ResumeParsed(
        filename=parsed["filename"], skills=parsed["skills"],
        years_experience=parsed["years"], chars=parsed["chars"],
        headline=sections.get("headline") or "", summary_drafted=summary_drafted,
        experience_added=added_exp, education_added=added_edu, projects_added=added_proj,
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
