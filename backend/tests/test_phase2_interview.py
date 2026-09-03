"""Phase 2 gate: a live panel interview runs over the WebSocket and PS11 R2 fires.

R2 is the problem statement's own example: a technically correct answer that never
states customer impact must move the floor to the product interviewer. It is a hard
rule, so this test is deterministic and does not need a Gemini key — live_client
falls back to a local connection when GEMINI_API_KEY is unset.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

JD = ("Senior Backend Engineer. You will design distributed systems in Python, own our "
      "Kafka pipelines, and run services on Kubernetes and AWS. Strong system design and "
      "API design required. You will mentor two engineers and join the on-call rotation.")

# Technically sound, and says nothing about who it helped or what it was worth.
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


@pytest.fixture(scope="module")
def world(client):
    """Employer posts and publishes a role; candidate applies. Returns the whole context."""
    emp = client.post("/api/employer/auth/register", json={
        "company_name": "Northwind Systems", "full_name": "Rea Patel",
        "email": "rea@northwind.com", "password": "correct-horse-battery",
    }).json()
    emp_h = {"Authorization": f"Bearer {emp['token']}"}

    job = client.post("/api/employer/jobs/", headers=emp_h, json={
        "title": "Senior Backend Engineer", "jd_text": JD,
    }).json()
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=emp_h)

    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Sam Okafor", "email": "sam@example.com",
        "password": "another-good-passphrase",
    }).json()
    cand_h = {"Authorization": f"Bearer {cand['token']}"}
    client.patch("/api/candidate/auth/me", headers=cand_h, json={
        "profile_sections_json": {
            "headline": "Backend engineer, 6 yrs",
            "summary": "Payments and streaming platforms.",
            "skills": ["Python", "Kafka", "Kubernetes"],
        },
    })
    app_row = client.post(f"/api/candidate/apply/{job['id']}", headers=cand_h).json()

    # The session is created here, not by a test, so every test below can run alone.
    started = client.post(
        f"/api/employer/applications/{app_row['id']}/start-interview", headers=emp_h)
    assert started.status_code == 201, started.text

    return {"emp_h": emp_h, "cand_h": cand_h, "cand_token": cand["token"],
            "job_id": job["id"], "app_id": app_row["id"],
            "session_id": started.json()["session_id"], "started": started.json()}


def test_employer_can_start_an_interview(client, world):
    body = world["started"]
    assert body["session_id"]
    assert body["job_title"] == "Senior Backend Engineer"
    keys = [p["key"] for p in body["panel"]]
    assert "tech" in keys and "product" in keys, keys
    assert body["agora_channel"].startswith("hireiq-")


def test_starting_twice_reuses_the_pending_session(client, world):
    """Pressing "Start interview" twice must not mint a second session on one stage.

    Duplicates would leave the candidate with two links to the same round and split
    the transcript across two rows.
    """
    r = client.post(f"/api/employer/applications/{world['app_id']}/start-interview",
                    headers=world["emp_h"])
    assert r.status_code == 201, r.text
    assert r.json()["session_id"] == world["session_id"], "a duplicate session was created"


def test_socket_refuses_to_open_without_disclosure(client, world):
    """The AI-disclosure gate is enforced server-side, not by the UI."""
    sid, token = world["session_id"], world["cand_token"]
    with pytest.raises(Exception):
        with client.websocket_connect(f"/api/interview/ws/{sid}?token={token}") as ws:
            ws.receive()


def test_socket_refuses_a_foreign_candidate(client, world):
    other = client.post("/api/candidate/auth/register", json={
        "full_name": "Mallory Vance", "email": "mallory@example.com",
        "password": "not-my-interview-1",
    }).json()
    with pytest.raises(Exception):
        with client.websocket_connect(
                f"/api/interview/ws/{world['session_id']}?token={other['token']}") as ws:
            ws.receive()


def test_disclosure_is_timestamped(client, world):
    r = client.post(f"/api/candidate/sessions/{world['session_id']}/consent",
                    headers=world["cand_h"])
    assert r.status_code == 200
    assert r.json()["accepted_at"] is not None

    detail = client.get(f"/api/candidate/sessions/{world['session_id']}",
                        headers=world["cand_h"]).json()
    assert detail["disclosure_accepted"] is True
    assert "conducted by AI" in detail["disclosure_text"]


def test_ps11_r2_fires_in_a_live_interview(client, world):
    """THE GATE. A correct answer with no impact hands the floor to the product interviewer."""
    sid, token = world["session_id"], world["cand_token"]
    # Idempotent: consent may already have been recorded by an earlier test.
    client.post(f"/api/candidate/sessions/{sid}/consent", headers=world["cand_h"])

    traces, floors, transcript = [], [], []
    answered = 0

    with client.websocket_connect(f"/api/interview/ws/{sid}?token={token}") as ws:
        for _ in range(400):
            msg = ws.receive()
            if msg.get("bytes") is not None or msg.get("text") is None:
                continue
            ev = json.loads(msg["text"])
            kind = ev.get("type")

            if kind == "floor":
                floors.append(ev.get("persona"))
            elif kind == "trace":
                traces.append(ev)
            elif kind == "transcript":
                transcript.append(ev)
            elif kind == "your_turn":
                if answered >= 3:
                    ws.send_text(json.dumps({"type": "end"}))
                    continue
                answered += 1
                ws.send_text(json.dumps({"type": "text", "text": CORRECT_NO_IMPACT}))
            elif kind == "ended":
                break
            elif kind == "error":
                raise AssertionError(f"Interview errored: {ev}")

    assert answered >= 1, "the candidate never got the floor"

    r2 = [t for t in traces if t.get("rule") == "R2"]
    assert r2, (
        "PS11 R2 did not fire. Rules seen: "
        f"{[(t.get('rule'), t.get('next'), t.get('intent')) for t in traces]}"
    )
    assert r2[0]["next"] == "product", r2[0]
    assert r2[0]["intent"] == "challenge", r2[0]
    assert "product" in floors, f"product interviewer never took the floor: {floors}"


def test_transcript_and_assessment_persisted(client, world):
    from app.db import SessionLocal
    from app.models import InterviewAssessment, InterviewSession, InterviewTurn

    db = SessionLocal()
    try:
        sess = db.get(InterviewSession, world["session_id"])
        assert sess.status == "ended"
        assert sess.disclosure_accepted_at is not None

        turns = db.query(InterviewTurn).filter(
            InterviewTurn.session_id == sess.id).order_by(InterviewTurn.seq).all()
        assert turns, "no turns were persisted"
        assert any(t.speaker == "candidate" for t in turns)
        assert any(t.speaker != "candidate" for t in turns)

        assessment = db.query(InterviewAssessment).filter(
            InterviewAssessment.session_id == sess.id).first()
        assert assessment is not None, "no assessment was generated"
        assert assessment.released_to_candidate in (False, 0), "feedback must be gated"
    finally:
        db.close()


# ===========================================================================
# R7 in a live interview — all eleven PS11 capabilities in one session
# ===========================================================================
@pytest.fixture(scope="module")
def scenario_world(client):
    """A fresh application with a Customer on the panel, so R7 has an owner."""
    from app.db import SessionLocal
    from app.models import Scenario

    db = SessionLocal()
    try:
        if db.query(Scenario).count() == 0:
            import scripts.seed_scenarios as seed  # noqa: PLC0415
            for row in seed.SCENARIOS:
                db.add(Scenario(**row))
            db.commit()
    finally:
        db.close()

    emp = client.post("/api/employer/auth/register", json={
        "company_name": "Westwind Data", "full_name": "Ivo Marek",
        "email": "ivo@westwind.com", "password": "a-good-long-passphrase"}).json()
    emp_h = {"Authorization": f"Bearer {emp['token']}"}

    job = client.post("/api/employer/jobs/", headers=emp_h, json={
        "title": "Platform Engineer", "jd_text": JD,
        "stages": [{"seq": 1, "name": "AI Loop", "kind": "ai_interview",
                    "interview_config_json": {
                        "panel": ["tech", "product", "customer", "hiring_manager"],
                        "preset": "loop", "start_difficulty": 3}}],
    }).json()
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=emp_h)

    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Dana Reyes", "email": "dana@example.com",
        "password": "yet-another-passphrase"}).json()
    cand_h = {"Authorization": f"Bearer {cand['token']}"}
    app_row = client.post(f"/api/candidate/apply/{job['id']}", headers=cand_h).json()

    started = client.post(
        f"/api/employer/applications/{app_row['id']}/start-interview", headers=emp_h).json()
    client.post(f"/api/candidate/sessions/{started['session_id']}/consent", headers=cand_h)

    return {"emp_h": emp_h, "cand_h": cand_h, "cand_token": cand["token"],
            "session_id": started["session_id"], "app_id": app_row["id"],
            "panel": [p["key"] for p in started["panel"]]}


def test_r7_opens_a_roleplay_in_a_live_interview(client, scenario_world):
    """PS11 #6 end to end: the Customer persona takes the floor in character."""
    assert "customer" in scenario_world["panel"], scenario_world["panel"]

    sid, token = scenario_world["session_id"], scenario_world["cand_token"]
    traces, floors, scenario_events = [], [], []
    answered = 0

    with client.websocket_connect(f"/api/interview/ws/{sid}?token={token}") as ws:
        for _ in range(800):
            msg = ws.receive()
            if msg.get("bytes") is not None or msg.get("text") is None:
                continue
            ev = json.loads(msg["text"])
            kind = ev.get("type")

            if kind == "floor":
                floors.append((ev.get("persona"), ev.get("intent")))
            elif kind == "trace":
                traces.append(ev)
            elif kind == "scenario":
                scenario_events.append(ev)
            elif kind == "your_turn":
                if answered >= 8:
                    ws.send_text(json.dumps({"type": "end"}))
                    continue
                answered += 1
                # Strong, specific, and it states impact — so R2 stays quiet and the
                # interview gets far enough for R7 to become the interesting rule.
                ws.send_text(json.dumps({"type": "text", "text": (
                    "I partitioned the Kafka topic by tenant and added a write-through "
                    "cache. Support tickets about stale dashboards dropped from 40 a week "
                    "to 3, which unblocked the Acme renewal. I owned the rollout and the "
                    "on-call runbook for it.")}))
            elif kind == "ended":
                break
            elif kind == "error":
                raise AssertionError(f"Interview errored: {ev}")

    rules = [t.get("rule") for t in traces]
    assert "R7" in rules, f"R7 never fired. Rules seen: {rules}"

    r7 = next(t for t in traces if t.get("rule") == "R7")
    assert r7["intent"] == "scenario", r7
    assert r7["next"] in ("customer", "hiring_manager"), r7

    assert scenario_events, "no scenario event was emitted for the UI"
    opened = scenario_events[0]
    assert opened["phase"] == "open"
    assert opened["title"], "the scenario has no title to show"
    assert opened["persona"] == r7["next"]

    # The owner must actually take the floor in character.
    assert any(intent == "scenario" for _, intent in floors), floors


def test_only_one_roleplay_runs_per_interview(client, scenario_world):
    """A second role-play would consume the coverage the panel still needs."""
    from app.db import SessionLocal
    from app.models import InterviewTurn

    db = SessionLocal()
    try:
        turns = db.query(InterviewTurn).filter(
            InterviewTurn.session_id == scenario_world["session_id"]).all()
    finally:
        db.close()
    opens = [t for t in turns if t.rule_fired == "R7"]
    assert len(opens) <= SC_MAX_OPENS, f"{len(opens)} role-plays opened"


SC_MAX_OPENS = 4   # one open + up to MAX_SCENARIO_TURNS in-character turns
