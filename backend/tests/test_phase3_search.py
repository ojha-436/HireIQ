"""Job search filters, resume matching, and employer-defined pipelines."""
from __future__ import annotations

import io
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

BACKEND_JD = ("Senior Backend Engineer. 5-8 years of experience. You will work in Python "
              "with Kafka on Kubernetes and AWS. Strong system design required.")
FRONTEND_JD = ("Frontend Engineer. At least 2 years experience with React and TypeScript. "
               "You will own our design system.")
LEAD_JD = "Engineering Manager. 10+ years. You will mentor engineers and own delivery."


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="module")
def world(client):
    emp = client.post("/api/employer/auth/register", json={
        "company_name": "Northwind Systems", "full_name": "Rea Patel",
        "email": "rea@northwind.com", "password": "correct-horse-battery"}).json()
    eh = {"Authorization": f"Bearer {emp['token']}"}

    ids = {}
    for key, title, jd, country, mode in [
        ("backend", "Senior Backend Engineer", BACKEND_JD, "India", "hybrid"),
        ("frontend", "Frontend Engineer", FRONTEND_JD, "Germany", "remote"),
        ("lead", "Engineering Manager", LEAD_JD, "India", "onsite"),
    ]:
        j = client.post("/api/employer/jobs/", headers=eh, json={
            "title": title, "jd_text": jd, "country": country,
            "remote_mode": mode, "location": country}).json()
        client.post(f"/api/employer/jobs/{j['id']}/publish", headers=eh)
        ids[key] = j["id"]

    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Sam Okafor", "email": "sam@example.com",
        "password": "another-good-passphrase"}).json()
    ch = {"Authorization": f"Bearer {cand['token']}"}
    return {"eh": eh, "ch": ch, "ids": ids}


# ------------------------------------------------------------------ experience parsing
def test_experience_range_parsed_from_the_jd(client, world):
    jobs = {j["title"]: j for j in client.get("/api/employer/jobs/", headers=world["eh"]).json()}
    assert (jobs["Senior Backend Engineer"]["min_experience_years"],
            jobs["Senior Backend Engineer"]["max_experience_years"]) == (5, 8)
    assert jobs["Frontend Engineer"]["min_experience_years"] == 2
    assert jobs["Engineering Manager"]["min_experience_years"] == 10
    assert jobs["Engineering Manager"]["max_experience_years"] is None   # "10+" is open-ended


# ------------------------------------------------------------------------ facets
def test_facets_come_from_open_postings_only(client, world):
    facets = client.get("/api/candidate/jobs", headers=world["ch"]).json()["facets"]
    assert facets["countries"] == ["Germany", "India"]
    assert set(facets["remote_modes"]) == {"hybrid", "onsite", "remote"}
    assert facets["total_open"] == 3
    assert "React" in facets["skills"] and "Kafka" in facets["skills"]


# ------------------------------------------------------------------------ filters
def test_country_filter(client, world):
    body = client.get("/api/candidate/jobs?country=Germany", headers=world["ch"]).json()
    assert [j["title"] for j in body["jobs"]] == ["Frontend Engineer"]


def test_remote_filter(client, world):
    body = client.get("/api/candidate/jobs?remote_mode=remote", headers=world["ch"]).json()
    assert [j["title"] for j in body["jobs"]] == ["Frontend Engineer"]


def test_skill_filter(client, world):
    body = client.get("/api/candidate/jobs?skills=React", headers=world["ch"]).json()
    assert [j["title"] for j in body["jobs"]] == ["Frontend Engineer"]


def test_experience_filter_is_a_range_overlap(client, world):
    """A candidate with 6 years should see the 5-8 role, not the 10+ one."""
    body = client.get("/api/candidate/jobs?min_experience=6&max_experience=6",
                      headers=world["ch"]).json()
    titles = [j["title"] for j in body["jobs"]]
    assert "Senior Backend Engineer" in titles
    assert "Engineering Manager" not in titles, "a 10+ role must not match 6 years"
    assert "Frontend Engineer" in titles, "a 2+ open-ended role still matches 6 years"


def test_free_text_search_covers_company_and_jd(client, world):
    assert client.get("/api/candidate/jobs?q=northwind",
                      headers=world["ch"]).json()["facets"]["total_open"] == 3
    body = client.get("/api/candidate/jobs?q=design%20system", headers=world["ch"]).json()
    assert [j["title"] for j in body["jobs"]] == ["Frontend Engineer"]


# ------------------------------------------------------------------- resume matching
def test_resume_upload_extracts_skills_and_years(client, world):
    resume = (b"Sam Okafor\nSenior Backend Engineer with 6 years of experience.\n"
              b"Built Python services with Kafka, deployed on Kubernetes and AWS.\n"
              b"Strong system design background.\n")
    r = client.post("/api/candidate/auth/me/resume", headers=world["ch"],
                    files={"file": ("sam.txt", io.BytesIO(resume), "text/plain")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["years_experience"] == 6
    assert {"Python", "Kafka", "Kubernetes", "AWS"} <= set(body["skills"])

    me = client.get("/api/candidate/auth/me", headers=world["ch"]).json()
    assert me["years_experience"] == 6
    assert "Kafka" in me["profile_sections_json"]["skills"]


def test_match_percent_appears_after_resume_upload(client, world):
    body = client.get("/api/candidate/jobs?sort=match", headers=world["ch"]).json()
    by_title = {j["title"]: j for j in body["jobs"]}

    backend = by_title["Senior Backend Engineer"]
    assert backend["match_pct"] is not None and backend["match_pct"] > 50
    assert "Kafka" in backend["matched_skills"]

    frontend = by_title["Frontend Engineer"]
    assert frontend["match_pct"] == 0, "no overlap with a React/TypeScript role"
    assert "React" in frontend["missing_skills"]

    # sort=match must put the best fit first
    assert body["jobs"][0]["title"] == "Senior Backend Engineer"
    assert body["profile_years"] == 6


def test_match_only_filter_drops_unscored_and_zero(client, world):
    body = client.get("/api/candidate/jobs?match_only=true", headers=world["ch"]).json()
    assert [j["title"] for j in body["jobs"]] == ["Senior Backend Engineer"]


def test_bad_resume_upload_is_refused_with_a_fix(client, world):
    r = client.post("/api/candidate/auth/me/resume", headers=world["ch"],
                    files={"file": ("photo.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")})
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]   # the message must say what to do instead


# ---------------------------------------------------------- employer-defined pipeline
def test_employer_can_define_the_pipeline_at_creation(client, world):
    """Question 2: choose which stages are AI and which are human, up front."""
    r = client.post("/api/employer/jobs/", headers=world["eh"], json={
        "title": "Staff Platform Engineer",
        "jd_text": "Kubernetes, Terraform, on-call. 8+ years.",
        "country": "India",
        "stages": [
            {"seq": 1, "name": "AI Screen", "kind": "ai_interview",
             "interview_config_json": {"panel": ["tech"], "preset": "screen",
                                       "start_difficulty": 2}},
            {"seq": 2, "name": "Hiring Manager Call", "kind": "human_interview"},
            {"seq": 3, "name": "AI Panel", "kind": "ai_interview",
             "interview_config_json": {"panel": ["tech", "product", "customer"],
                                       "preset": "panel", "start_difficulty": 4}},
        ],
    })
    assert r.status_code == 201, r.text
    stages = r.json()["stages"]
    assert [(s["seq"], s["kind"]) for s in stages] == [
        (1, "ai_interview"), (2, "human_interview"), (3, "ai_interview")]
    assert stages[0]["interview_config_json"]["start_difficulty"] == 2
    assert stages[2]["interview_config_json"]["panel"] == ["tech", "product", "customer"]


def test_unknown_stage_kind_is_rejected(client, world):
    """A typo'd kind would silently never run, so it must fail loudly at the edge."""
    r = client.post("/api/employer/jobs/", headers=world["eh"], json={
        "title": "Bad Pipeline", "jd_text": "Python.",
        "stages": [{"seq": 1, "name": "Vibes Round", "kind": "vibe_check"}],
    })
    assert r.status_code == 422
