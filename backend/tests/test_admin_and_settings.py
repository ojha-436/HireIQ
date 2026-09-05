"""Admin section, account settings, AI job-description drafting, resume-to-profile
parsing, and role/skill-based practice — the second round of additions on top of the
PS11 engine and the practice-mode work in test_practice_mode.py.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app

RESUME_TEXT = """Jordan Casey
Senior Backend Engineer

SUMMARY
Backend engineer with six years building payments infrastructure at scale.

EXPERIENCE
Senior Backend Engineer - Acme Corp (2021-2024)
Led the migration of the payments pipeline to an event-driven architecture on Kafka.

Backend Engineer - Globex Inc (2018-2021)
Built and operated REST APIs serving 10M requests per day.

EDUCATION
B.Tech Computer Science - IIT Bombay (2014-2018)

SKILLS
Python, Kafka, Kubernetes, SQL
"""


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def admin_headers(client):
    r = client.post("/api/admin/auth/login", json={"username": "admin", "password": "admin@123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _new_employer(client, company_name="Acme Testing Co"):
    email = f"emp-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/employer/auth/register", json={
        "company_name": company_name, "full_name": "Employer One",
        "email": email, "password": "correct-horse-battery",
    })
    body = r.json()
    return body, email, {"Authorization": f"Bearer {body['token']}"}


def _new_candidate(client):
    email = f"cand-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/candidate/auth/register", json={
        "full_name": "Candidate One", "email": email, "password": "another-good-passphrase",
    })
    body = r.json()
    return body, email, {"Authorization": f"Bearer {body['token']}"}


# =========================================================================== admin auth
def test_admin_seed_login_works(admin_headers):
    assert admin_headers


def test_admin_login_rejects_wrong_password(client):
    r = client.post("/api/admin/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_employer_token_cannot_call_admin_routes(client):
    _, _, emp_h = _new_employer(client)
    r = client.get("/api/admin/employers", headers=emp_h)
    assert r.status_code == 401


def test_admin_token_cannot_call_employer_routes(client, admin_headers):
    r = client.get("/api/employer/jobs/", headers=admin_headers)
    assert r.status_code == 401


def test_admin_can_change_own_password_and_old_one_stops_working(client, admin_headers):
    r = client.patch("/api/admin/auth/me/password", headers=admin_headers,
                     json={"current_password": "admin@123", "new_password": "new-admin-pass-1"})
    assert r.status_code == 200

    stale = client.post("/api/admin/auth/login", json={"username": "admin", "password": "admin@123"})
    assert stale.status_code == 401

    fresh = client.post("/api/admin/auth/login", json={"username": "admin", "password": "new-admin-pass-1"})
    assert fresh.status_code == 200

    # Leave it as the seed expects for any other test module sharing this DB file.
    fresh_h = {"Authorization": f"Bearer {fresh.json()['token']}"}
    client.patch("/api/admin/auth/me/password", headers=fresh_h,
                json={"current_password": "new-admin-pass-1", "new_password": "admin@123"})


# =========================================================================== manage employers/candidates
def test_admin_can_list_and_suspend_an_employer(client, admin_headers):
    company = f"Suspend Test {uuid.uuid4().hex[:6]}"
    _, email, emp_h = _new_employer(client, company_name=company)
    listing = client.get("/api/admin/employers", headers=admin_headers).json()
    row = next(r for r in listing if r["name"] == company)
    assert row["active"] is True

    toggled = client.post(f"/api/admin/employers/{row['id']}/toggle-active", headers=admin_headers)
    assert toggled.status_code == 200
    assert toggled.json()["active"] is False

    # An already-issued token stops working immediately, not just at next login.
    still = client.get("/api/employer/auth/me", headers=emp_h)
    assert still.status_code == 403

    relogin = client.post("/api/employer/auth/login", json={
        "email": email, "password": "correct-horse-battery",
    })
    assert relogin.status_code == 403


def test_admin_can_list_and_suspend_a_candidate(client, admin_headers):
    body, email, cand_h = _new_candidate(client)
    listing = client.get("/api/admin/candidates", headers=admin_headers).json()
    row = next(r for r in listing if r["email"] == email)
    assert row["is_active"] is True

    toggled = client.post(f"/api/admin/candidates/{row['id']}/toggle-active", headers=admin_headers)
    assert toggled.status_code == 200
    assert toggled.json()["is_active"] is False

    still = client.get("/api/candidate/auth/me", headers=cand_h)
    assert still.status_code == 403

    login_again = client.post("/api/candidate/auth/login", json={"email": email, "password": "another-good-passphrase"})
    assert login_again.status_code == 403


# =========================================================================== health / kpis
def test_health_and_kpis_require_admin_auth(client):
    assert client.get("/api/admin/health").status_code == 401
    assert client.get("/api/admin/kpis").status_code == 401


def test_health_reports_real_config_state(client, admin_headers):
    h = client.get("/api/admin/health", headers=admin_headers).json()
    assert h["status"] == "ok"
    assert h["database"]["connected"] is True
    assert h["gemini_configured"] is False   # blanked by conftest for the whole suite
    assert isinstance(h["live_interview_sessions"], int)


def test_kpis_are_real_aggregates(client, admin_headers):
    _new_employer(client)
    _new_candidate(client)
    k = client.get("/api/admin/kpis", headers=admin_headers).json()
    assert k["employers"]["total"] >= 1
    assert k["candidates"]["total"] >= 1
    assert "interviews" in k and "practice" in k["interviews"] and "hiring" in k["interviews"]


# =========================================================================== account password change
def test_employer_and_candidate_can_change_their_own_password(client):
    emp_body, _, emp_h = _new_employer(client)
    r = client.patch("/api/employer/auth/me/password", headers=emp_h,
                     json={"current_password": "correct-horse-battery", "new_password": "brand-new-password-1"})
    assert r.status_code == 200
    wrong = client.patch("/api/employer/auth/me/password", headers=emp_h,
                         json={"current_password": "not-it", "new_password": "whatever12345"})
    assert wrong.status_code == 400

    cand_body, email, cand_h = _new_candidate(client)
    r2 = client.patch("/api/candidate/auth/me/password", headers=cand_h,
                      json={"current_password": "another-good-passphrase", "new_password": "brand-new-password-2"})
    assert r2.status_code == 200
    relogin = client.post("/api/candidate/auth/login", json={"email": email, "password": "brand-new-password-2"})
    assert relogin.status_code == 200


# =========================================================================== AI job-description drafting
def test_generate_description_offline_fallback_is_a_real_draft(client):
    _, _, emp_h = _new_employer(client)
    r = client.post("/api/employer/jobs/generate-description", headers=emp_h, json={
        "title": "Senior Backend Engineer", "department": "Platform", "seniority": "senior",
        "keywords": ["Kafka", "Kubernetes"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "template"   # no GEMINI_API_KEY in the test environment
    assert "Backend Engineer" in body["jd_text"]
    assert "Responsibilities" in body["jd_text"] or "What you will do" in body["jd_text"]
    assert len(body["jd_text"]) > 200


def test_generate_description_requires_employer_auth(client):
    r = client.post("/api/employer/jobs/generate-description", json={"title": "Engineer"})
    assert r.status_code == 401


# =========================================================================== job editing
def test_employer_can_edit_a_job_after_creation(client):
    _, _, emp_h = _new_employer(client)
    job = client.post("/api/employer/jobs/", headers=emp_h, json={
        "title": "Data Analyst", "jd_text": "Analyze data using SQL and Python.",
    }).json()

    updated = client.patch(f"/api/employer/jobs/{job['id']}", headers=emp_h, json={
        "title": "Senior Data Analyst", "location": "Remote",
        "jd_text": "Analyze data using SQL, Python and Kubernetes.",
    })
    assert updated.status_code == 200
    body = updated.json()
    assert body["title"] == "Senior Data Analyst"
    assert body["location"] == "Remote"
    assert "Kubernetes" in body["required_skills_json"]

    fetched = client.get(f"/api/employer/jobs/{job['id']}", headers=emp_h).json()
    assert fetched["title"] == "Senior Data Analyst"


def test_employer_cannot_edit_another_tenants_job(client):
    _, _, emp_h_a = _new_employer(client)
    job = client.post("/api/employer/jobs/", headers=emp_h_a, json={"title": "Role A"}).json()
    _, _, emp_h_b = _new_employer(client)
    r = client.patch(f"/api/employer/jobs/{job['id']}", headers=emp_h_b, json={"title": "Hijacked"})
    assert r.status_code == 404


# =========================================================================== resume -> profile
def test_resume_upload_drafts_a_full_profile_not_just_skills(client):
    _, email, cand_h = _new_candidate(client)
    r = client.post(
        "/api/candidate/auth/me/resume", headers=cand_h,
        files={"file": ("resume.txt", RESUME_TEXT.encode(), "text/plain")},
    )
    assert r.status_code == 200, r.text
    parsed = r.json()
    assert "Engineer" in parsed["headline"]
    assert parsed["experience_added"] >= 1
    assert parsed["education_added"] >= 1

    me = client.get("/api/candidate/auth/me", headers=cand_h).json()
    sections = me["profile_sections_json"]
    assert sections["headline"]
    assert sections["summary"]
    assert len(sections["experience"]) >= 1
    assert sections["experience"][0]["title"]
    assert len(sections["education"]) >= 1
    assert "Python" in sections["skills"]


def test_resume_upload_never_overwrites_hand_typed_headline(client):
    _, email, cand_h = _new_candidate(client)
    client.patch("/api/candidate/auth/me", headers=cand_h, json={
        "profile_sections_json": {"headline": "My own words", "summary": "", "skills": [], "experience": []},
    })
    client.post(
        "/api/candidate/auth/me/resume", headers=cand_h,
        files={"file": ("resume.txt", RESUME_TEXT.encode(), "text/plain")},
    )
    me = client.get("/api/candidate/auth/me", headers=cand_h).json()
    assert me["profile_sections_json"]["headline"] == "My own words"


# =========================================================================== role vs skill practice
def test_role_based_practice_grounds_on_a_real_job(client):
    _, _, emp_h = _new_employer(client)
    job = client.post("/api/employer/jobs/", headers=emp_h, json={
        "title": "Platform Engineer", "jd_text": "Own our Kubernetes and Kafka platform.",
    }).json()
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=emp_h)

    _, email, cand_h = _new_candidate(client)
    started = client.post("/api/candidate/practice/start", headers=cand_h, json={"job_id": job["id"]})
    assert started.status_code == 201, started.text
    assert started.json()["job_title"] == "Platform Engineer"


def test_skill_based_practice_has_no_job_title(client):
    _, email, cand_h = _new_candidate(client)
    started = client.post("/api/candidate/practice/start", headers=cand_h,
                          json={"skill_names": ["Python", "SQL"]})
    assert started.status_code == 201
    assert started.json()["job_title"] == ""
