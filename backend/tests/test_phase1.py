"""Phase 1 gate: employer posts a job -> candidate applies -> appears in employer's list.

Also asserts the two auth audiences are genuinely isolated and tenants cannot see each other.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient


from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


JD = """
We are hiring a Senior Backend Engineer. You will design distributed systems in Python,
own our Kafka pipelines, run services on Kubernetes and AWS, and mentor two engineers.
Strong system design and API design skills required. On-call rotation included.
"""


@pytest.fixture(scope="module")
def employer(client):
    r = client.post(
        "/api/employer/auth/register",
        json={
            "company_name": "Northwind Systems",
            "domain": "northwind.com",
            "industry": "SaaS",
            "size_band": "50-200",
            "full_name": "Rea Patel",
            "email": "rea@northwind.com",
            "password": "correct-horse-battery",
        },
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def candidate(client):
    r = client.post(
        "/api/candidate/auth/register",
        json={
            "full_name": "Sam Okafor",
            "email": "sam@example.com",
            "password": "another-good-passphrase",
        },
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_jd_skill_extraction_and_default_panel(client, employer):
    r = client.post(
        "/api/employer/jobs/",
        headers=employer,
        json={"title": "Senior Backend Engineer", "location": "Bengaluru", "jd_text": JD},
    )
    assert r.status_code == 201, r.text
    job = r.json()

    skills = job["required_skills_json"]
    assert "Python" in skills and "Kafka" in skills and "Kubernetes" in skills
    assert "System Design" in skills

    # A default pipeline is proposed, and the AI stage carries a panel + start difficulty.
    assert len(job["stages"]) == 2
    ai_stage = job["stages"][0]
    assert ai_stage["kind"] == "ai_interview"
    cfg = ai_stage["interview_config_json"]
    assert "tech" in cfg["panel"] and "product" in cfg["panel"]
    assert cfg["start_difficulty"] == 3

    # Mentorship in the JD should pull the hiring manager onto the panel.
    assert "hiring_manager" in cfg["panel"]


def test_draft_job_is_invisible_to_candidates(client, employer, candidate):
    # Scoped to this test's own tenant: the suite shares a database, and a test that
    # assumes it owns every row breaks the moment another module seeds a job.
    body = client.get("/api/candidate/jobs", headers=candidate).json()
    mine = [j for j in body["jobs"] if j["company_name"] == "Northwind Systems"]
    assert mine == [], "an unpublished draft must never reach the public board"


def test_full_phase1_flow(client, employer, candidate):
    job_id = client.get("/api/employer/jobs/", headers=employer).json()[0]["id"]

    published = client.post(f"/api/employer/jobs/{job_id}/publish", headers=employer)
    assert published.status_code == 200
    assert published.json()["status"] == "open"

    board = [j for j in client.get("/api/candidate/jobs", headers=candidate).json()["jobs"]
             if j["company_name"] == "Northwind Systems"]
    assert len(board) == 1
    assert board[0]["already_applied"] is False

    applied = client.post(f"/api/candidate/apply/{job_id}", headers=candidate)
    assert applied.status_code == 201, applied.text
    # The candidate lands on stage 1 of the pipeline, not in limbo.
    assert applied.json()["current_stage_name"] == "AI Panel Interview"

    # THE GATE: the applicant shows up on the employer side.
    applicants = client.get(f"/api/employer/jobs/{job_id}/applications", headers=employer).json()
    assert len(applicants) == 1
    assert applicants[0]["full_name"] == "Sam Okafor"
    assert applicants[0]["status"] == "applied"

    assert client.get("/api/candidate/me/applications", headers=candidate).json()[0][
        "job_title"
    ] == "Senior Backend Engineer"


def test_duplicate_application_rejected(client, candidate):
    job_id = next(j["id"] for j in
                  client.get("/api/candidate/jobs", headers=candidate).json()["jobs"]
                  if j["company_name"] == "Northwind Systems")
    r = client.post(f"/api/candidate/apply/{job_id}", headers=candidate)
    assert r.status_code == 409


def test_pipeline_edit_blocked_once_candidates_are_in_it(client, employer):
    job_id = client.get("/api/employer/jobs/", headers=employer).json()[0]["id"]
    r = client.put(
        f"/api/employer/jobs/{job_id}/pipeline",
        headers=employer,
        json={"stages": [{"seq": 1, "name": "Only Stage", "kind": "ai_interview"}]},
    )
    assert r.status_code == 409
    assert "orphan" in r.json()["detail"]


def test_audience_isolation(client, employer, candidate):
    """An employer token must be structurally unusable on candidate routes, and vice versa."""
    assert client.get("/api/candidate/jobs", headers=employer).status_code == 401
    assert client.get("/api/employer/jobs/", headers=candidate).status_code == 401
    assert client.get("/api/employer/jobs/").status_code == 401


def test_cross_tenant_job_is_not_found(client, employer):
    r = client.post(
        "/api/employer/auth/register",
        json={
            "company_name": "Rival Corp",
            "full_name": "Ada Rivera",
            "email": "ada@rival.com",
            "password": "yet-another-passphrase",
        },
    )
    rival = {"Authorization": f"Bearer {r.json()['token']}"}
    job_id = client.get("/api/employer/jobs/", headers=employer).json()[0]["id"]

    # 404 not 403 — we don't confirm the resource exists to a stranger.
    assert client.get(f"/api/employer/jobs/{job_id}", headers=rival).status_code == 404
    assert client.get("/api/employer/jobs/", headers=rival).json() == []


def test_login_does_not_leak_account_existence(client):
    unknown = client.post(
        "/api/employer/auth/login", json={"email": "nobody@nowhere.com", "password": "whatever12"}
    )
    wrong_pw = client.post(
        "/api/employer/auth/login", json={"email": "rea@northwind.com", "password": "wrong-one12"}
    )
    assert unknown.status_code == wrong_pw.status_code == 401
    assert unknown.json()["detail"] == wrong_pw.json()["detail"]


# ---------------------------------------------------------------- regression guards
def test_spa_is_served_from_the_api_origin(client):
    """The SPA must be served by this app.

    A separate static server means a second port, and a second port means the frontend
    can silently talk to a different backend — which is exactly the bug this guards.
    """
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>HireIQ" in r.text


def test_frontend_does_not_hardcode_an_api_port():
    """The client must default to the page's own origin, never a fixed port.

    This is the test that would have caught the real defect: the browser fell back to
    :8000 (a different service entirely) while every Playwright run injected an override
    to :8001, so the broken default was never exercised.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    html = (root / "frontend" / "index.html").read_text()
    api_js = (root / "frontend" / "js" / "api.js").read_text()

    assert "window.HIREIQ_API_BASE = localStorage.getItem('hireiq.apiBase') || '';" in html, (
        "index.html must default HIREIQ_API_BASE to '' (same origin)")
    for bad in ("http://127.0.0.1:8000", "http://localhost:8000",
                "http://127.0.0.1:8001", "http://localhost:8001"):
        assert bad not in html, f"index.html hardcodes {bad}"
        assert bad not in api_js, f"api.js hardcodes {bad}"
