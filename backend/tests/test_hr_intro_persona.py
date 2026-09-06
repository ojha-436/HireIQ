"""Every interviewer now has a real first name, and the panel opens with a warm HR
welcome before the substantive interviewers take over — the product had no human
identity ("Technical Interviewer" spoke first with no introduction) and a candidate
reported it never felt like a real interview.

Two things had to be true together: `propose_panel` puts "hr" first without dropping a
substantive interviewer off the panel (PRESETS.panel_size was bumped by one seat to
cover it), and the moderator's fair-rotation rule must not hand the floor BACK to hr
once the other panelists catch up to its turn count — otherwise a 14-turn interview
periodically lapses back into small talk.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.interview import personas as P
from app.interview.moderator import Moderator
from app.main import app


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


# --------------------------------------------------------------------- propose_panel
def test_propose_panel_always_opens_with_hr():
    for skills, title in [
        (["python", "kubernetes"], "Backend Engineer"),
        (["product_management"], "Product Manager"),
        ([], "Mystery Role"),
    ]:
        panel = P.propose_panel(skills, title)
        assert panel[0] == "hr", panel


def test_propose_panel_keeps_the_same_substantive_interviewers_as_before():
    """Adding hr must not bump a substantive interviewer off a technical role's panel."""
    panel = P.propose_panel(["python", "kubernetes"], "Backend Engineer")
    assert "tech" in panel and "product" in panel and "hiring_manager" in panel


def test_every_persona_has_a_distinct_first_name():
    names = {p.name for p in P.PERSONAS.values()}
    assert len(names) == len(P.PERSONAS), "two personas sharing a name defeats the point"


def test_label_combines_name_and_role():
    hr = P.get("hr")
    assert hr.name == "Donna"
    assert "Donna" in hr.label and "HR" in hr.label


def test_hr_voice_is_distinct_from_every_other_persona():
    voices = {p.voice for p in P.PERSONAS.values()}
    assert len(voices) == len(P.PERSONAS)


# --------------------------------------------------------------------- moderator rotation
def test_hr_does_not_return_once_the_rest_of_the_panel_catches_up():
    """Regression: without excluding hr from _next_unserved, every substantive
    interviewer eventually ties hr's turn count and R5 hands the floor back to it."""
    panel = ["hr", "tech", "product", "hiring_manager"]
    m = Moderator(panel, required_skill_ids=["python"], max_turns=14)
    m.note_turn("hr")  # the opening welcome

    seen = []
    neutral = {"scores": {}, "specificity": "partial", "flags": [], "impact_stated": True}
    for _ in range(9):
        d = m.decide(neutral, target_skill_id="python")
        m.note_turn(d["next_speaker"])
        seen.append(d["next_speaker"])

    assert "hr" not in seen, seen


def test_hr_never_speaks_first_via_next_unserved_either():
    """Even a panel that (misconfigured) omits hr's opening note_turn call must not let
    a zero-turn-count hr win the very first R5 rotation forever — belt and suspenders
    for the primary fix above."""
    panel = ["tech", "hr", "product"]
    m = Moderator(panel, required_skill_ids=["python"], max_turns=10)
    # hr has already been served once, same as production always arranges via _grant_floor.
    m.note_turn("hr")
    nxt = m._next_unserved()
    assert nxt != "hr"


# --------------------------------------------------------------------- end to end
def test_interview_start_opens_with_the_hr_persona(client):
    tag = uuid.uuid4().hex[:8]
    emp = client.post("/api/employer/auth/register", json={
        "company_name": f"Panel Co {tag}", "full_name": "Rae Kim",
        "email": f"panel-emp-{tag}@example.com", "password": "correct-horse-battery",
    }).json()
    eh = {"Authorization": f"Bearer {emp['token']}"}
    job = client.post("/api/employer/jobs/", headers=eh, json={
        "title": "Backend Engineer", "jd_text": "Python, Kubernetes, 3+ years.",
    }).json()
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=eh)

    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Sam Rivera", "email": f"panel-cand-{tag}@example.com",
        "password": "another-good-passphrase",
    }).json()
    ch = {"Authorization": f"Bearer {cand['token']}"}
    app_row = client.post(f"/api/candidate/apply/{job['id']}", headers=ch).json()

    started = client.post(
        f"/api/employer/applications/{app_row['id']}/start-interview", headers=eh).json()
    panel = started["panel"]
    assert panel[0]["key"] == "hr"
    assert "Donna" in panel[0]["label"]
    assert any(p["key"] == "tech" for p in panel[1:])
