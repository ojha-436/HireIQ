"""Employer live monitor: SSE stream, Panel Memory, whisper (W0), and tenant isolation."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuditLog, Scenario

JD = ("Senior Backend Engineer. 5-8 years. Python, Kafka, Kubernetes, AWS, Redis. "
      "Strong system design. Mentor engineers and join the on-call rotation.")


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
        "company_name": "Monitor Co", "full_name": "Ada Lin",
        "email": "ada@monitor.com", "password": "a-long-enough-passphrase"}).json()
    emp_h = {"Authorization": f"Bearer {emp['token']}"}

    job = client.post("/api/employer/jobs/", headers=emp_h, json={
        "title": "Senior Backend Engineer", "jd_text": JD,
        "stages": [{"seq": 1, "name": "AI Panel", "kind": "ai_interview",
                    "interview_config_json": {"panel": ["tech", "product", "customer"],
                                              "preset": "panel", "start_difficulty": 3}}],
    }).json()
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=emp_h)

    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Noor Haddad", "email": "noor@example.com",
        "password": "another-long-passphrase"}).json()
    cand_h = {"Authorization": f"Bearer {cand['token']}"}
    app_row = client.post(f"/api/candidate/apply/{job['id']}", headers=cand_h).json()

    started = client.post(
        f"/api/employer/applications/{app_row['id']}/start-interview", headers=emp_h).json()
    client.post(f"/api/candidate/sessions/{started['session_id']}/consent", headers=cand_h)

    return {"emp_h": emp_h, "cand_h": cand_h, "cand_token": cand["token"],
            "session_id": started["session_id"], "app_id": app_row["id"],
            "tenant_email": "ada@monitor.com"}


# ------------------------------------------------------------------- isolation
def test_another_tenant_cannot_monitor(client, world):
    rival = client.post("/api/employer/auth/register", json={
        "company_name": "Rival Group", "full_name": "Ben Cole",
        "email": "ben@rival.com", "password": "rival-long-passphrase"}).json()
    rival_h = {"Authorization": f"Bearer {rival['token']}"}

    # 404, not 403 — a stranger is not told the session exists.
    r = client.get(f"/api/employer/sessions/{world['session_id']}/panel-memory",
                   headers=rival_h)
    assert r.status_code == 404
    assert client.post(f"/api/employer/whisper/{world['session_id']}",
                       headers=rival_h, json={"text": "hello"}).status_code == 404


def test_candidate_token_cannot_monitor(client, world):
    r = client.get(f"/api/employer/sessions/{world['session_id']}/panel-memory",
                   headers=world["cand_h"])
    assert r.status_code == 401


# -------------------------------------------------------------------- whisper
def test_whisper_is_refused_when_the_interview_is_not_live(client, world):
    r = client.post(f"/api/employer/whisper/{world['session_id']}",
                    headers=world["emp_h"], json={"text": "Ask about on-call."})
    assert r.status_code == 409
    assert "not live" in r.json()["detail"]


def test_empty_whisper_is_refused_with_a_usable_message(client, world):
    r = client.post(f"/api/employer/whisper/{world['session_id']}",
                    headers=world["emp_h"], json={"text": "   "})
    assert r.status_code == 400
    assert "question" in r.json()["detail"].lower()


def test_overlong_whisper_is_refused(client, world):
    r = client.post(f"/api/employer/whisper/{world['session_id']}",
                    headers=world["emp_h"], json={"text": "x" * 401})
    assert r.status_code == 400
    assert "400 characters" in r.json()["detail"]


# ------------------------------------------- panel memory during a live session
def test_panel_memory_and_whisper_during_a_live_interview(client, world):
    """The load-bearing test: shared context is observable, and W0 reaches the panel."""
    sid, token = world["session_id"], world["cand_token"]
    ANSWER = ("I partitioned the Kafka topic by tenant and added a write-through cache, "
              "cutting p99 from 800ms to 90ms.")

    memories, traces, whispered = [], [], False
    answered = 0

    with client.websocket_connect(f"/api/interview/ws/{sid}?token={token}") as ws:
        for _ in range(600):
            msg = ws.receive()
            if msg.get("bytes") is not None or msg.get("text") is None:
                continue
            ev = json.loads(msg["text"])
            kind = ev.get("type")

            if kind == "panel_memory":
                memories.append(ev)
            elif kind == "trace":
                traces.append(ev)
            elif kind == "your_turn":
                # Whisper once the interview is genuinely live and has state to carry.
                if answered == 1 and not whispered:
                    r = client.post(f"/api/employer/whisper/{sid}", headers=world["emp_h"],
                                    json={"text": "Ask who was paged when this broke."})
                    assert r.status_code == 200, r.text
                    whispered = True
                if answered >= 4:
                    ws.send_text(json.dumps({"type": "end"}))
                    continue
                answered += 1
                ws.send_text(json.dumps({"type": "text", "text": ANSWER}))
            elif kind == "ended":
                break
            elif kind == "error":
                raise AssertionError(f"Interview errored: {ev}")

    assert whispered, "the whisper was never sent"
    assert memories, "no panel_memory events reached the monitor stream"

    latest = memories[-1]
    assert "facts" in latest and "open_threads" in latest and "coverage" in latest
    assert latest["difficulty"]["level"] >= 1
    assert latest["turns_by_persona"], "coverage per persona was empty"

    # The established number from the answer must be carried as a shared fact.
    fact_values = " ".join(str(f.get("value")) for f in latest["facts"])
    assert "800" in fact_values or "90" in fact_values, latest["facts"]

    # W0 must appear in the rule trace — the whisper actually influenced turn-taking.
    assert "W0" in [t.get("rule") for t in traces], [t.get("rule") for t in traces]


def test_whisper_is_written_to_the_audit_log(client, world):
    """A human influenced this interview; the record has to say so."""
    db = SessionLocal()
    try:
        rows = db.query(AuditLog).filter(AuditLog.action == "interview.whisper").all()
    finally:
        db.close()
    assert rows, "no audit entry for the whisper"
    entry = rows[-1]
    assert entry.payload_json["session_id"] == world["session_id"]
    assert "paged" in entry.payload_json["text"]
    assert entry.actor_id is not None, "an audit row with no actor is not an audit row"


# ===================================================== spoken-output hygiene
# The invariants forbid markdown and self-narration, and the live model violated both
# anyway — it prefixed a turn with "**Probing Product Impact**" and then described its
# own strategy. Every character reaches the candidate's ears, so this is enforced in
# code, not left to the prompt.
@pytest.mark.parametrize("raw, expected", [
    ("**Probing Product Impact** What did it change for the customer?",
     "What did it change for the customer?"),
    ("[Challenge] Who was paged when this broke?",
     "Who was paged when this broke?"),
    ("(Follow-up): What broke first?", "What broke first?"),
    ("**Step 1** **Probing** Why Redis?", "Why Redis?"),
    ("That works, but what did it change?", "That works, but what did it change?"),
])
def test_stage_directions_are_stripped(raw, expected):
    from app.interview.session import clean_spoken
    assert clean_spoken(raw) == expected


def test_markdown_never_reaches_the_candidate():
    from app.interview.session import clean_spoken
    out = clean_spoken("## Heading\n- bullet one\n- `code` two\n**bold** tail")
    for token in ("##", "**", "`", "- "):
        assert token not in out, f"{token!r} survived: {out!r}"


def test_plain_speech_is_left_alone():
    from app.interview.session import clean_spoken
    plain = "So when the cache went stale, who noticed first?"
    assert clean_spoken(plain) == plain


def test_contradiction_note_does_not_duplicate_the_unit():
    """The captured token already carries its unit; appending it produced '800ms ms'."""
    from app.interview.analyst import analyse_local

    first = analyse_local(answer="We cut it to 800ms across the board.", persona="tech",
                          turn_id="t1", required_skill_names=["Python"])
    established = {"numbers": {}}
    for token in first.get("numbers") or []:
        import re
        key = re.sub(r"[\d.,]", "", str(token)).strip().lower()
        if key:
            established["numbers"][key] = {"value": str(token).strip(), "turn_id": "t1"}

    second = analyse_local(answer="Actually it was 90ms.", persona="tech", turn_id="t2",
                           established=established, required_skill_names=["Python"])
    notes = [f["note"] for f in second["flags"] if f["type"] == "contradiction"]
    assert notes, second["flags"]
    assert "ms ms" not in notes[0], notes[0]
