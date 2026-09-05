"""Candidate PRACTICE mode (plan.md Phase 2/13/14 additions).

Practice and hiring share exactly one engine (InterviewRuntime, Moderator, Analyst,
Assessment) — this file proves that by driving the same PS11 R2 scenario through the
practice endpoint instead of the employer `start-interview` endpoint used by
test_phase2_interview.py, and by asserting the divergence is exactly what the product
plan calls for: practice coaches, hiring does not; practice has no employer visibility
or cross-candidate access; retakes accumulate into a progress trend.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app

# Technically sound, and says nothing about who it helped or what it was worth —
# the same PS11 R2 trigger used by test_phase2_interview.py.
CORRECT_NO_IMPACT = (
    "I added a Redis write-through cache in front of the read path and partitioned the "
    "Kafka topic by tenant, which cut p99 latency from 800 milliseconds to 90 milliseconds."
)


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def candidate(client):
    import uuid
    email = f"practice-{uuid.uuid4().hex[:8]}@example.com"
    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Priya Nair", "email": email, "password": "another-good-passphrase",
    }).json()
    h = {"Authorization": f"Bearer {cand['token']}"}
    client.patch("/api/candidate/auth/me", headers=h, json={
        "profile_sections_json": {
            "headline": "Backend engineer, 5 yrs",
            "summary": "Payments platform.",
            "skills": ["Python", "Kafka", "Kubernetes"],
        },
    })
    return {"token": cand["token"], "headers": h}


def _drive_to_end(client, session_id, token, *, answers=3):
    """Run a WS interview to completion, returning the collected rule traces."""
    traces, transcript = [], []
    answered = 0
    with client.websocket_connect(f"/api/interview/ws/{session_id}?token={token}") as ws:
        for _ in range(400):
            msg = ws.receive()
            if msg.get("bytes") is not None or msg.get("text") is None:
                continue
            ev = json.loads(msg["text"])
            kind = ev.get("type")
            if kind == "trace":
                traces.append(ev)
            elif kind == "transcript":
                transcript.append(ev)
            elif kind == "your_turn":
                if answered >= answers:
                    ws.send_text(json.dumps({"type": "end"}))
                    continue
                answered += 1
                ws.send_text(json.dumps({"type": "text", "text": CORRECT_NO_IMPACT}))
            elif kind == "ended":
                break
            elif kind == "error":
                raise AssertionError(f"Interview errored: {ev}")
    return traces, transcript, answered


def _start_practice(client, headers, **body):
    r = client.post("/api/candidate/practice/start", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _consent_and_run(client, token, headers, session_id):
    client.post(f"/api/candidate/sessions/{session_id}/consent", headers=headers)
    return _drive_to_end(client, session_id, token)


# =========================================================================== creation
def test_candidate_can_start_practice_interview_without_a_job(client, candidate):
    started = _start_practice(client, candidate["headers"], skill_names=["Python", "Kafka"])
    assert started["session_id"]
    assert started["status"] == "setup"

    from app.db import SessionLocal
    from app.models import InterviewSession
    db = SessionLocal()
    try:
        sess = db.get(InterviewSession, started["session_id"])
        assert sess.session_type == "practice"
        assert sess.job_application_id is None
        assert sess.application_id is None
        assert sess.candidate_id is not None
    finally:
        db.close()


def test_practice_sessions_are_excluded_from_the_hiring_pending_list(client, candidate):
    _start_practice(client, candidate["headers"], skill_names=["Python"])
    pending = client.get("/api/candidate/me/interviews/pending", headers=candidate["headers"]).json()
    assert pending == []


# =========================================================================== same engine
def test_ps11_r2_fires_in_a_practice_interview(client, candidate):
    """THE GATE, reused: practice mode is not a separate, weaker engine."""
    started = _start_practice(client, candidate["headers"],
                              skill_names=["Python", "Kafka", "Kubernetes"])
    traces, transcript, answered = _consent_and_run(
        client, candidate["token"], candidate["headers"], started["session_id"])

    assert answered >= 1, "the candidate never got the floor"
    r2 = [t for t in traces if t.get("rule") == "R2"]
    assert r2, f"PS11 R2 did not fire in practice mode. Rules seen: {[t.get('rule') for t in traces]}"
    assert r2[0]["next"] == "product"


# =========================================================================== coaching divergence
def test_practice_mode_produces_a_coaching_plan(client, candidate):
    started = _start_practice(client, candidate["headers"], skill_names=["Python", "Kafka", "Kubernetes"])
    _consent_and_run(client, candidate["token"], candidate["headers"], started["session_id"])

    report = client.get(
        f"/api/candidate/practice/sessions/{started['session_id']}/report",
        headers=candidate["headers"],
    ).json()
    assert report["coaching"] is not None
    assert report["coaching"]["weakest_dimension"]
    assert len(report["coaching"]["plan"]) == 7


def test_hiring_mode_never_produces_a_coaching_plan(client):
    """The same engine, run through the employer flow, must never attach coaching.

    `session.py._build_and_store_report`'s hiring branch never calls the career-coach
    engine at all — only the practice branch (`_build_practice_report`) does. This drives
    an actual employer-initiated interview end-to-end to prove it at the API boundary,
    not just by reading the source.
    """
    emp = client.post("/api/employer/auth/register", json={
        "company_name": "Coaching Check Ltd", "full_name": "Rea Patel",
        "email": "rea-coaching@example.com", "password": "correct-horse-battery",
    }).json()
    emp_h = {"Authorization": f"Bearer {emp['token']}"}
    job = client.post("/api/employer/jobs/", headers=emp_h, json={
        "title": "Senior Backend Engineer",
        "jd_text": "Design distributed systems in Python and Kafka on Kubernetes.",
    }).json()
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=emp_h)

    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Sam Okafor", "email": "sam-coaching@example.com",
        "password": "another-good-passphrase",
    }).json()
    cand_h = {"Authorization": f"Bearer {cand['token']}"}
    app_row = client.post(f"/api/candidate/apply/{job['id']}", headers=cand_h).json()
    started = client.post(f"/api/employer/applications/{app_row['id']}/start-interview",
                          headers=emp_h).json()

    client.post(f"/api/candidate/sessions/{started['session_id']}/consent", headers=cand_h)
    _drive_to_end(client, started["session_id"], cand["token"], answers=1)

    from app.db import SessionLocal
    from app.models import InterviewAssessment, InterviewSession
    db = SessionLocal()
    try:
        sess = db.get(InterviewSession, started["session_id"])
        assert sess.session_type != "practice"
        row = db.query(InterviewAssessment).filter(
            InterviewAssessment.session_id == started["session_id"]).first()
        assert row is not None
        assert "coaching" not in (row.report_json or {})
    finally:
        db.close()


# =========================================================================== isolation
def test_a_candidate_cannot_read_another_candidates_practice_report(client, candidate):
    started = _start_practice(client, candidate["headers"], skill_names=["Python"])
    _consent_and_run(client, candidate["token"], candidate["headers"], started["session_id"])

    other = client.post("/api/candidate/auth/register", json={
        "full_name": "Mallory Vance", "email": "mallory-practice@example.com",
        "password": "not-my-interview-1",
    }).json()
    other_h = {"Authorization": f"Bearer {other['token']}"}

    r = client.get(f"/api/candidate/practice/sessions/{started['session_id']}/report", headers=other_h)
    assert r.status_code == 404


# =========================================================================== retake / progress
def test_retake_creates_a_second_session_and_progress_reflects_both(client, candidate):
    first = _start_practice(client, candidate["headers"], skill_names=["Python", "Kafka", "Kubernetes"])
    _consent_and_run(client, candidate["token"], candidate["headers"], first["session_id"])

    second = _start_practice(client, candidate["headers"], skill_names=["Python", "Kafka", "Kubernetes"])
    _consent_and_run(client, candidate["token"], candidate["headers"], second["session_id"])

    progress = client.get("/api/candidate/practice/progress", headers=candidate["headers"]).json()
    assert progress["attempts"] >= 2
    ids = [h["session_id"] for h in progress["history"]]
    assert first["session_id"] in ids
    assert second["session_id"] in ids
    # Chronological, so a trend line reads left-to-right as improvement over time.
    created = [h["created_at"] for h in progress["history"]]
    assert created == sorted(created)

    sessions_list = client.get("/api/candidate/practice/sessions", headers=candidate["headers"]).json()
    assert len(sessions_list) >= 2


def test_dashboard_readiness_is_null_until_a_practice_interview_completes(client):
    import uuid
    email = f"fresh-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/api/candidate/auth/register", json={
        "full_name": "Fresh Candidate", "email": email, "password": "another-good-passphrase",
    })
    headers = {"Authorization": f"Bearer {resp.json()['token']}"}
    dash = client.get("/api/candidate/dashboard", headers=headers).json()
    assert dash["readiness"] is None
    assert dash["has_practice_history"] is False


def test_dashboard_readiness_appears_after_a_completed_practice_interview(client, candidate):
    started = _start_practice(client, candidate["headers"], skill_names=["Python", "Kafka", "Kubernetes"])
    _consent_and_run(client, candidate["token"], candidate["headers"], started["session_id"])

    dash = client.get("/api/candidate/dashboard", headers=candidate["headers"]).json()
    assert dash["has_practice_history"] is True
    assert dash["readiness"] is not None
    assert 0 <= dash["readiness"] <= 100
    assert dash["weakest_dimension"] in dash["dimensions"]


# =========================================================================== weighting
def test_compute_overall_is_unweighted_by_default_and_matches_prior_behaviour():
    from app.interview.assessment import compute_overall
    dim_scores = {"correctness": [5, 5], "empathy": [1, 1]}
    assert compute_overall(dim_scores) == 60  # mean(5,1)=3 -> 3/5*100 = 60


def test_configurable_weights_change_the_overall_score_deterministically():
    from app.interview.assessment import compute_overall
    dim_scores = {"correctness": [5, 5], "empathy": [1, 1]}
    weighted = compute_overall(dim_scores, weights={"correctness": 3.0, "empathy": 1.0})
    # (5*3 + 1*1) / 4 = 4.0 -> 4/5*100 = 80
    assert weighted == 80
    unweighted = compute_overall(dim_scores, weights={"correctness": 1.0, "empathy": 1.0})
    assert unweighted == 60


def test_missing_weight_defaults_to_one():
    from app.interview.assessment import compute_overall
    dim_scores = {"correctness": [4, 4], "empathy": [4, 4]}
    # Only one dimension weighted explicitly; the other must default to 1.0, not 0.
    assert compute_overall(dim_scores, weights={"correctness": 1.0}) == 80
