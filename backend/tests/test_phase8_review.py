"""Phase 6: assessment review, decisions with reasons, and the feedback gate."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuditLog, InterviewAssessment, Scenario

JD = ("Senior Backend Engineer. 5-8 years. Python, Kafka, Kubernetes, AWS, Redis. "
      "Strong system design. Mentor engineers and join the on-call rotation.")
ANSWER = ("I partitioned the Kafka topic by tenant and added a write-through cache, "
          "cutting p99 latency from 800ms to 90ms.")


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Scenario).count() == 0:
            import scripts.seed_scenarios as seed  # noqa: PLC0415
            for row in seed.SCENARIOS:
                db.add(Scenario(**row))
            db.commit()
    finally:
        db.close()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def world(client):
    emp = client.post("/api/employer/auth/register", json={
        "company_name": "Review Labs", "full_name": "Kit Rao",
        "email": "kit@reviewlabs.com", "password": "a-sufficiently-long-pass"}).json()
    emp_h = {"Authorization": f"Bearer {emp['token']}"}

    job = client.post("/api/employer/jobs/", headers=emp_h, json={
        "title": "Senior Backend Engineer", "jd_text": JD,
        "stages": [
            {"seq": 1, "name": "AI Screen", "kind": "ai_interview",
             "interview_config_json": {"panel": ["tech", "product"], "preset": "screen",
                                       "start_difficulty": 3}},
            {"seq": 2, "name": "Human Round", "kind": "human_interview"},
        ],
    }).json()
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=emp_h)

    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Rune Halvorsen", "email": "rune@example.com",
        "password": "another-sufficient-pass"}).json()
    cand_h = {"Authorization": f"Bearer {cand['token']}"}
    app_row = client.post(f"/api/candidate/apply/{job['id']}", headers=cand_h).json()

    started = client.post(
        f"/api/employer/applications/{app_row['id']}/start-interview", headers=emp_h).json()
    sid = started["session_id"]
    client.post(f"/api/candidate/sessions/{sid}/consent", headers=cand_h)

    # Run a real interview so there is something to review.
    answered = 0
    with client.websocket_connect(f"/api/interview/ws/{sid}?token={cand['token']}") as ws:
        for _ in range(500):
            msg = ws.receive()
            if msg.get("bytes") is not None or msg.get("text") is None:
                continue
            ev = json.loads(msg["text"])
            if ev.get("type") == "your_turn":
                if answered >= 3:
                    ws.send_text(json.dumps({"type": "end"}))
                    continue
                answered += 1
                ws.send_text(json.dumps({"type": "text", "text": ANSWER}))
            elif ev.get("type") == "ended":
                break

    return {"emp_h": emp_h, "cand_h": cand_h, "app_id": app_row["id"],
            "session_id": sid, "job_id": job["id"]}


# =============================================================== review packet
def test_review_packet_has_scores_evidence_and_transcript(client, world):
    r = client.get(f"/api/employer/applications/{world['app_id']}/assessment",
                   headers=world["emp_h"])
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["candidate"]["full_name"] == "Rune Halvorsen"
    assert body["job"]["title"] == "Senior Backend Engineer"
    assert len(body["rounds"]) == 1

    rnd = body["rounds"][0]
    assert rnd["stage_name"] == "AI Screen"
    assert rnd["disclosure_accepted_at"], "the consent timestamp must be on the record"
    assert rnd["transcript"], "no transcript in the review packet"
    assert rnd["assessment"] is not None
    assert 0 <= rnd["assessment"]["overall"] <= 100


def test_every_evidence_line_cites_a_real_turn(client, world):
    """An assessment line without turn_ids is an opinion, not evidence."""
    body = client.get(f"/api/employer/applications/{world['app_id']}/assessment",
                      headers=world["emp_h"]).json()
    rnd = body["rounds"][0]
    turn_ids = {t["id"] for t in rnd["transcript"]}
    evidence = rnd["assessment"]["evidence"]

    assert evidence, "no cited evidence lines"
    for line in evidence:
        assert line["turn_ids"], line
        assert any(t in turn_ids for t in line["turn_ids"]), (
            f"citation points at no real turn: {line}")


def test_another_tenant_cannot_read_the_assessment(client, world):
    rival = client.post("/api/employer/auth/register", json={
        "company_name": "Nosy Corp", "full_name": "Vic Stern",
        "email": "vic@nosy.com", "password": "nosy-long-passphrase"}).json()
    r = client.get(f"/api/employer/applications/{world['app_id']}/assessment",
                   headers={"Authorization": f"Bearer {rival['token']}"})
    assert r.status_code == 404


# ================================================================ the gate
def test_feedback_is_withheld_before_any_decision(client, world):
    body = client.get(f"/api/candidate/me/applications/{world['app_id']}",
                      headers=world["cand_h"]).json()
    assert body["feedback"] == [], "feedback leaked before the employer released it"
    assert body["feedback_pending"] is True, (
        "an assessment exists, so the candidate must be told it is pending")
    # Where they stand is never withheld.
    assert body["timeline"], "the candidate cannot see their own progress"
    assert body["status_label"]


def test_candidate_sees_stage_progress(client, world):
    body = client.get(f"/api/candidate/me/applications/{world['app_id']}",
                      headers=world["cand_h"]).json()
    states = {t["name"]: t["state"] for t in body["timeline"]}
    assert states["AI Screen"] == "done", states
    assert "Human Round" in states


def test_another_candidate_cannot_read_the_application(client, world):
    other = client.post("/api/candidate/auth/register", json={
        "full_name": "Mara Volk", "email": "mara@example.com",
        "password": "not-my-application-1"}).json()
    r = client.get(f"/api/candidate/me/applications/{world['app_id']}",
                   headers={"Authorization": f"Bearer {other['token']}"})
    assert r.status_code == 404


# =============================================================== decisions
def test_a_decision_without_a_reason_is_refused(client, world):
    r = client.post(f"/api/employer/applications/{world['app_id']}/advance",
                    headers=world["emp_h"], json={"reason": "   "})
    assert r.status_code == 400
    assert "reason" in r.json()["detail"].lower()


def test_advance_moves_the_stage_and_releases_feedback(client, world):
    r = client.post(f"/api/employer/applications/{world['app_id']}/advance",
                    headers=world["emp_h"],
                    json={"reason": "Strong on Kafka and caching; want the HM to probe scope.",
                          "release_feedback": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "in_progress"
    assert body["moved_to"] == "Human Round"
    assert body["assessments_released"] == 1

    seen = client.get(f"/api/candidate/me/applications/{world['app_id']}",
                      headers=world["cand_h"]).json()
    assert seen["feedback"], "feedback was released but the candidate cannot see it"
    assert seen["feedback_pending"] is False
    fb = seen["feedback"][0]
    assert fb["overall"] is not None
    assert fb["evidence"], "released feedback with no evidence is not evidence-based"
    for line in fb["evidence"]:
        assert line["quote"], "a citation resolved to no quote"
    assert fb["ai_disclosure"], "layer 4 of the AI disclosure is missing from the report"


def test_percentile_is_hidden_below_five_sessions(client, world):
    """A benchmark computed from one session is not a benchmark."""
    fb = client.get(f"/api/candidate/me/applications/{world['app_id']}",
                    headers=world["cand_h"]).json()["feedback"][0]
    assert fb["percentile_n"] < 5
    assert fb["percentile"] is None, "a percentile was shown with too small a sample"


def test_every_decision_is_audited_with_its_reason(client, world):
    db = SessionLocal()
    try:
        rows = db.query(AuditLog).filter(
            AuditLog.action == "application.advance",
            AuditLog.subject_id == world["app_id"]).all()
    finally:
        db.close()
    assert rows, "no audit entry for the advance"
    assert "Kafka" in rows[-1].payload_json["reason"]
    assert rows[-1].actor_id is not None


# ================================================================ rejection
@pytest.fixture(scope="module")
def rejected(client, world):
    """A second application taken all the way to a rejection with feedback."""
    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Sol Adeyemi", "email": "sol@example.com",
        "password": "third-sufficient-pass"}).json()
    cand_h = {"Authorization": f"Bearer {cand['token']}"}
    app_row = client.post(f"/api/candidate/apply/{world['job_id']}", headers=cand_h).json()

    started = client.post(
        f"/api/employer/applications/{app_row['id']}/start-interview",
        headers=world["emp_h"]).json()
    sid = started["session_id"]
    client.post(f"/api/candidate/sessions/{sid}/consent", headers=cand_h)

    answered = 0
    with client.websocket_connect(f"/api/interview/ws/{sid}?token={cand['token']}") as ws:
        for _ in range(500):
            msg = ws.receive()
            if msg.get("bytes") is not None or msg.get("text") is None:
                continue
            ev = json.loads(msg["text"])
            if ev.get("type") == "your_turn":
                if answered >= 2:
                    ws.send_text(json.dumps({"type": "end"}))
                    continue
                answered += 1
                ws.send_text(json.dumps({"type": "text", "text": "We used best practices."}))
            elif ev.get("type") == "ended":
                break

    r = client.post(f"/api/employer/applications/{app_row['id']}/reject",
                    headers=world["emp_h"],
                    json={"reason": "Answers stayed generic; no specifics on any system."})
    assert r.status_code == 200, r.text
    return {"cand_h": cand_h, "app_id": app_row["id"], "decision": r.json()}


def test_rejection_releases_feedback_by_default(client, rejected):
    """A rejected candidate has nothing left to game, and being told why is the point."""
    assert rejected["decision"]["status"] == "rejected"
    assert rejected["decision"]["assessments_released"] >= 1

    body = client.get(f"/api/candidate/me/applications/{rejected['app_id']}",
                      headers=rejected["cand_h"]).json()
    assert body["status"] == "rejected"
    assert body["status_label"] == "Not moving forward"
    assert body["feedback"], "rejected with no feedback released"
    assert body["feedback"][0]["evidence"], "rejection feedback with no evidence"


def test_rejection_closes_the_remaining_stages(client, rejected):
    body = client.get(f"/api/candidate/me/applications/{rejected['app_id']}",
                      headers=rejected["cand_h"]).json()
    states = [t["state"] for t in body["timeline"]]
    assert "closed" in states, states
    assert "upcoming" not in states, "a rejected candidate still shows upcoming stages"


def test_release_without_a_decision_works_for_a_live_candidate(client, world):
    r = client.post(f"/api/employer/applications/{world['app_id']}/release-feedback",
                    headers=world["emp_h"])
    assert r.status_code == 200
    assert r.json()["assessments_released"] == 0, "already released; nothing to re-release"


def test_offer_stage_leaves_nothing_upcoming(client, world):
    """A candidate at offer has cleared the process; showing stages still ahead is wrong."""
    # Advance until the pipeline is exhausted.
    for _ in range(4):
        r = client.post(f"/api/employer/applications/{world['app_id']}/advance",
                        headers=world["emp_h"], json={"reason": "Cleared this round."})
        if r.json().get("status") == "offer":
            break

    body = client.get(f"/api/candidate/me/applications/{world['app_id']}",
                      headers=world["cand_h"]).json()
    assert body["status"] == "offer"
    states = [t["state"] for t in body["timeline"]]
    assert "upcoming" not in states, states
