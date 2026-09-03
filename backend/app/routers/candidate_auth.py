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


@router.post("/me/resume", response_model=ResumeParsed)
async def upload_resume(
    file: UploadFile = File(...),
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> ResumeParsed:
    """Parse a resume into skills + years, and fold the result into the profile.

    The extracted text is NOT stored. Only the derived skills and year count are kept,
    which is all that job matching and the interview grounding actually need.
    """
    blob = await file.read()
    try:
        parsed = parse_resume(file.filename or "resume", blob)
    except ResumeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    sections = dict(cand.profile_sections_json or {})
    merged = list(dict.fromkeys([*(sections.get("skills") or []), *parsed["skills"]]))
    sections["skills"] = merged
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
    )
