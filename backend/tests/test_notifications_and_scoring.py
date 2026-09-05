"""Candidate notifications (the read side lives in routers/notifications.py; the write
side is scattered at every point something changes for a candidate without them acting:
start-interview, advance/reject, release-feedback, auto-advance) and the employer
applicant list now carrying a score, which is what actually lets an employer "select
the best candidate" from a list instead of opening every review page one at a time.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app

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
def world(client):
    """Employer posts and publishes a role; candidate applies. Returns the whole context."""
    emp = client.post("/api/employer/auth/register", json={
        "company_name": f"Notif Co {uuid.uuid4().hex[:6]}", "full_name": "Rea Patel",
        "email": f"rea-{uuid.uuid4().hex[:8]}@example.com", "password": "correct-horse-battery",
    }).json()
    emp_h = {"Authorization": f"Bearer {emp['token']}"}

    job = client.post("/api/employer/jobs/", headers=emp_h, json={
        "title": "Senior Backend Engineer",
        "jd_text": "Design distributed systems in Python and Kafka on Kubernetes.",
    }).json()
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=emp_h)

    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Sam Okafor", "email": f"sam-{uuid.uuid4().hex[:8]}@example.com",
        "password": "another-good-passphrase",
    }).json()
    cand_h = {"Authorization": f"Bearer {cand['token']}"}
    app_row = client.post(f"/api/candidate/apply/{job['id']}", headers=cand_h).json()

    return {"emp_h": emp_h, "cand_h": cand_h, "cand_token": cand["token"],
            "job_id": job["id"], "app_id": app_row["id"]}


def _drive_one_answer_to_end(client, session_id, token, cand_h):
    client.post(f"/api/candidate/sessions/{session_id}/consent", headers=cand_h)
    answered = False
    with client.websocket_connect(f"/api/interview/ws/{session_id}?token={token}") as ws:
        for _ in range(300):
            msg = ws.receive()
            if msg.get("bytes") is not None or msg.get("text") is None:
                continue
            ev = json.loads(msg["text"])
            if ev.get("type") == "your_turn":
                if answered:
                    ws.send_text(json.dumps({"type": "end"}))
                    continue
                answered = True
                ws.send_text(json.dumps({"type": "text", "text": CORRECT_NO_IMPACT}))
            elif ev.get("type") == "ended":
                break
            elif ev.get("type") == "error":
                raise AssertionError(f"Interview errored: {ev}")


# =========================================================================== notifications
def test_starting_an_interview_notifies_the_candidate(client, world):
    started = client.post(f"/api/employer/applications/{world['app_id']}/start-interview",
                          headers=world["emp_h"])
    assert started.status_code == 201, started.text

    notifs = client.get("/api/candidate/me/notifications", headers=world["cand_h"]).json()
    assert notifs["unread_count"] >= 1
    kinds = [n["kind"] for n in notifs["notifications"]]
    assert "interview_ready" in kinds
    row = next(n for n in notifs["notifications"] if n["kind"] == "interview_ready")
    assert row["payload"]["job_title"] == "Senior Backend Engineer"
    assert row["read"] is False


def test_mark_read_and_mark_all_read(client, world):
    client.post(f"/api/employer/applications/{world['app_id']}/start-interview", headers=world["emp_h"])
    notifs = client.get("/api/candidate/me/notifications", headers=world["cand_h"]).json()
    first_id = notifs["notifications"][0]["id"]

    r = client.post(f"/api/candidate/me/notifications/{first_id}/read", headers=world["cand_h"])
    assert r.status_code == 200
    refreshed = client.get("/api/candidate/me/notifications", headers=world["cand_h"]).json()
    row = next(n for n in refreshed["notifications"] if n["id"] == first_id)
    assert row["read"] is True

    r2 = client.post("/api/candidate/me/notifications/read-all", headers=world["cand_h"])
    assert r2.status_code == 200
    final = client.get("/api/candidate/me/notifications", headers=world["cand_h"]).json()
    assert final["unread_count"] == 0


def test_a_candidate_cannot_read_or_mark_another_candidates_notifications(client, world):
    client.post(f"/api/employer/applications/{world['app_id']}/start-interview", headers=world["emp_h"])
    mine = client.get("/api/candidate/me/notifications", headers=world["cand_h"]).json()
    notif_id = mine["notifications"][0]["id"]

    other = client.post("/api/candidate/auth/register", json={
        "full_name": "Mallory Vance", "email": f"mallory-{uuid.uuid4().hex[:8]}@example.com",
        "password": "not-my-notification-1",
    }).json()
    other_h = {"Authorization": f"Bearer {other['token']}"}

    r = client.post(f"/api/candidate/me/notifications/{notif_id}/read", headers=other_h)
    assert r.status_code == 404
    theirs = client.get("/api/candidate/me/notifications", headers=other_h).json()
    assert theirs["notifications"] == []


def test_advance_and_release_feedback_notify_the_candidate(client, world):
    started = client.post(f"/api/employer/applications/{world['app_id']}/start-interview",
                          headers=world["emp_h"]).json()
    _drive_one_answer_to_end(client, started["session_id"], world["cand_token"], world["cand_h"])

    r = client.post(f"/api/employer/applications/{world['app_id']}/advance",
                    headers=world["emp_h"], json={"reason": "Strong technical answer."})
    assert r.status_code == 200, r.text

    notifs = client.get("/api/candidate/me/notifications", headers=world["cand_h"]).json()
    kinds = [n["kind"] for n in notifs["notifications"]]
    assert "application_decision" in kinds
    row = next(n for n in notifs["notifications"] if n["kind"] == "application_decision")
    assert row["payload"]["job_title"] == "Senior Backend Engineer"


# =========================================================================== applicant scores
def test_applicant_list_has_no_score_before_any_interview(client, world):
    rows = client.get(f"/api/employer/jobs/{world['job_id']}/applications", headers=world["emp_h"]).json()
    assert len(rows) == 1
    assert rows[0]["overall"] is None
    assert rows[0]["recommendation"] is None


def test_applicant_list_shows_the_score_after_assessment(client, world):
    started = client.post(f"/api/employer/applications/{world['app_id']}/start-interview",
                          headers=world["emp_h"]).json()
    _drive_one_answer_to_end(client, started["session_id"], world["cand_token"], world["cand_h"])

    rows = client.get(f"/api/employer/jobs/{world['job_id']}/applications", headers=world["emp_h"]).json()
    row = next(r for r in rows if r["id"] == world["app_id"])
    assert isinstance(row["overall"], int)
    assert row["recommendation"]
