"""Request/response contracts. Pydantic v2."""
from __future__ import annotations

from datetime import datetime

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


# ------------------------------------------------------------------ employer auth
class EmployerRegister(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    domain: str | None = None
    industry: str | None = None
    size_band: str | None = None
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    token: str
    role: str
    expires_in: int


class EmployerMe(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    tenant_id: int
    tenant_name: str


class PasswordChange(BaseModel):
    """Shared by candidate, employer and admin — same rule, same shape everywhere."""
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


# ------------------------------------------------------------------ admin
class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str


class AdminMe(BaseModel):
    id: int
    username: str


class EmployerAdminOut(BaseModel):
    id: int
    name: str
    domain: str | None
    active: bool
    plan: str
    created_at: datetime
    job_count: int
    user_count: int


class CandidateAdminOut(BaseModel):
    id: int
    full_name: str
    email: str
    is_active: bool
    created_at: datetime
    application_count: int


class JobDescriptionGenerate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    department: str | None = None
    seniority: str | None = None
    keywords: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ jobs
class StageIn(BaseModel):
    seq: int = Field(ge=1, le=12)
    name: str = Field(min_length=1, max_length=120)
    # Guarded rather than free text: an unknown kind would silently never run.
    kind: Literal["ai_interview", "human_interview", "screening_call"] = "ai_interview"
    interview_config_json: dict = Field(default_factory=dict)


class JobCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    department: str | None = None
    location: str | None = None
    country: str | None = None
    remote_mode: str = "onsite"           # onsite | hybrid | remote
    employment_type: str = "full_time"
    jd_text: str = ""
    min_experience_years: int | None = None
    max_experience_years: int | None = None
    stages: list[StageIn] | None = None   # None = accept the proposed default pipeline


class JobUpdate(BaseModel):
    title: str | None = None
    department: str | None = None
    location: str | None = None
    country: str | None = None
    remote_mode: str | None = None
    employment_type: str | None = None
    jd_text: str | None = None
    required_skills_json: list[str] | None = None
    min_experience_years: int | None = None
    max_experience_years: int | None = None


class StageOut(BaseModel):
    id: int
    seq: int
    name: str
    kind: str
    interview_config_json: dict


class JobOut(BaseModel):
    id: int
    title: str
    department: str | None
    location: str | None
    country: str | None
    remote_mode: str
    employment_type: str
    min_experience_years: int | None
    max_experience_years: int | None
    jd_text: str
    required_skills_json: list[str]
    status: str
    published_at: datetime | None
    created_at: datetime
    applicant_count: int = 0
    stages: list[StageOut] = Field(default_factory=list)


class PipelineReplace(BaseModel):
    stages: list[StageIn]


# ------------------------------------------------------------------ candidate
class CandidateRegister(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    phone: str | None = None


class CandidateMe(BaseModel):
    id: int
    email: str
    full_name: str
    phone: str | None
    country: str | None = None
    years_experience: float | None = None
    profile_sections_json: dict
    resume_meta_json: dict = Field(default_factory=dict)


class CandidateProfileUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    country: str | None = None
    years_experience: float | None = None
    profile_sections_json: dict | None = None


class PublicJobOut(BaseModel):
    id: int
    title: str
    department: str | None
    location: str | None
    country: str | None
    remote_mode: str
    employment_type: str
    jd_text: str
    required_skills_json: list[str]
    company_name: str
    published_at: datetime | None
    min_experience_years: int | None = None
    max_experience_years: int | None = None
    stage_names: list[str] = Field(default_factory=list)
    stages: list[dict] = Field(default_factory=list)
    already_applied: bool = False
    # Present only once the candidate has skills on file. None = not scored.
    match_pct: int | None = None
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)


class JobFacets(BaseModel):
    """Filter options derived from what is actually open — never a hardcoded list."""
    countries: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    remote_modes: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    total_open: int = 0


class JobSearchOut(BaseModel):
    jobs: list[PublicJobOut]
    facets: JobFacets
    profile_skills: list[str] = Field(default_factory=list)
    profile_years: float | None = None


class ResumeParsed(BaseModel):
    filename: str
    skills: list[str]
    years_experience: float | None
    chars: int
    # Phase 3: what was drafted into the profile from the resume text itself, not just
    # skills/years. Counts reflect what was actually NEW — existing hand-typed entries
    # are never overwritten, so a re-upload can legitimately add zero of everything.
    headline: str = ""
    summary_drafted: bool = False
    experience_added: int = 0
    education_added: int = 0
    projects_added: int = 0


class ApplicationOut(BaseModel):
    id: int
    job_id: int
    job_title: str
    company_name: str
    status: str
    current_stage_name: str | None
    applied_at: datetime
    last_activity_at: datetime


class ApplicantOut(BaseModel):
    id: int
    candidate_id: int
    full_name: str
    email: str
    headline: str | None
    status: str
    current_stage_id: int | None
    applied_at: datetime
    # From the most recent interview session on this application, if one has been
    # assessed yet. None (not 0) when there is nothing to show — an unscored
    # applicant must never look like a 0.
    overall: int | None = None
    recommendation: str | None = None
